#!/usr/bin/env python3
"""Collect and normalize Sunsu Commerce Coupang seller products.

The collector intentionally preserves blocked/empty responses as unresolved rather
than treating them as completed or No-KC. It accepts multiple discovery paths and
writes deterministic shard diagnostics for later merge jobs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

SELLER_ID = "A01593407"
SELLER_NAME = "순수커머스"
BASE = "https://www.coupang.com"
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/127.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
}
PRODUCT_RE = re.compile(r"/vp/products/(\d+)")
ITEM_RE = re.compile(r"(?:itemId|item_id)[=:](\d+)")
VENDOR_RE = re.compile(r"(?:vendorItemId|vendor_item_id)[=:](\d+)")
BLOCK_MARKERS = (
    "access denied",
    "captcha",
    "비정상적인 접근",
    "잠시 후 다시",
    "forbidden",
)


@dataclass(slots=True)
class ProductRecord:
    productId: str
    itemId: str = ""
    vendorItemId: str = ""
    productName: str = ""
    productUrl: str = ""
    category: str = ""
    brand: str = ""
    publisherManufacturer: str = ""
    isbn: str = ""
    kcNumber: str = ""
    kcText: str = ""
    sourceUrls: list[str] = field(default_factory=list)
    responseState: str = "discovered"
    evidenceType: str = ""
    checkedAt: str = ""

    def merge(self, other: "ProductRecord") -> None:
        for name in (
            "itemId", "vendorItemId", "productName", "productUrl", "category",
            "brand", "publisherManufacturer", "isbn", "kcNumber", "kcText",
            "evidenceType",
        ):
            if not getattr(self, name) and getattr(other, name):
                setattr(self, name, getattr(other, name))
        self.sourceUrls = sorted(set(self.sourceUrls + other.sourceUrls))
        if self.responseState != "ok" and other.responseState == "ok":
            self.responseState = "ok"
        self.checkedAt = max(self.checkedAt, other.checkedAt)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_name(value: str) -> str:
    value = re.sub(r"\s+", " ", value or "").strip()
    return value.replace("０", "0").replace("１", "1")


def ids_from_url(url: str) -> tuple[str, str, str]:
    parsed = urlparse(url)
    product = PRODUCT_RE.search(parsed.path)
    query = parse_qs(parsed.query)
    item = (query.get("itemId") or query.get("item_id") or [""])[0]
    vendor = (query.get("vendorItemId") or query.get("vendor_item_id") or [""])[0]
    return (product.group(1) if product else "", item, vendor)


def response_state(response: requests.Response) -> str:
    if response.status_code in (401, 403, 429):
        return "blocked"
    if response.status_code >= 500:
        return "server_error"
    text = response.text.strip()
    if not text:
        return "empty"
    lower = text.lower()
    if any(marker in lower for marker in BLOCK_MARKERS):
        return "blocked"
    return "ok" if response.ok else f"http_{response.status_code}"


def discovery_urls() -> list[str]:
    return [
        f"https://shop.coupang.com/{SELLER_ID}?locale=ko_KR&platform=p",
        f"https://shop.coupang.com/{SELLER_ID}?locale=ko_KR&platform=m",
        f"{BASE}/np/search?q={SELLER_NAME}",
        f"{BASE}/np/search?q=%EC%88%9C%EC%88%98%EC%BB%A4%EB%A8%B8%EC%8A%A4",
    ]


def extract_links(html: str, source_url: str) -> list[ProductRecord]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[ProductRecord] = []
    candidates = set()
    for tag in soup.select("a[href]"):
        href = tag.get("href", "")
        if "/vp/products/" in href:
            candidates.add(href if href.startswith("http") else BASE + href)
    candidates.update(BASE + m.group(0) for m in PRODUCT_RE.finditer(html))
    for url in candidates:
        product_id, item_id, vendor_id = ids_from_url(url)
        if not product_id:
            continue
        out.append(ProductRecord(
            productId=product_id,
            itemId=item_id,
            vendorItemId=vendor_id,
            productUrl=url,
            sourceUrls=[source_url],
            evidenceType="seller-or-search-index",
            checkedAt=now_iso(),
        ))
    return out


def text_after_label(soup: BeautifulSoup, labels: Iterable[str]) -> str:
    for label in labels:
        node = soup.find(string=re.compile(rf"^\s*{re.escape(label)}\s*$", re.I))
        if node and node.parent:
            sibling = node.parent.find_next_sibling()
            if sibling:
                return normalize_name(sibling.get_text(" ", strip=True))
    return ""


def parse_detail(record: ProductRecord, html: str) -> ProductRecord:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.select_one("h1.prod-buy-header__title") or soup.select_one("meta[property='og:title']")
    if title:
        record.productName = normalize_name(title.get("content", "") if title.name == "meta" else title.get_text(" ", strip=True))
    record.brand = text_after_label(soup, ["브랜드"])
    record.publisherManufacturer = text_after_label(soup, ["제조자(수입자)", "제조사", "출판사"])
    page_text = soup.get_text(" ", strip=True)
    isbn = re.search(r"(?:ISBN(?:-13)?\s*[:：]?\s*)(97[89][0-9 -]{10,16})", page_text, re.I)
    if isbn:
        record.isbn = re.sub(r"\D", "", isbn.group(1))[:13]
    kc = re.search(r"\b(?:CB|CA|SU|U)\d{3,}[A-Z0-9-]{5,}\b", page_text, re.I)
    if kc:
        record.kcNumber = kc.group(0).upper()
    kc_text = re.search(r"(.{0,80}(?:KC|안전확인|어린이제품).{0,120})", page_text, re.I)
    if kc_text:
        record.kcText = normalize_name(kc_text.group(1))
    return record


def fetch(session: requests.Session, url: str, retries: int = 3) -> requests.Response | None:
    for attempt in range(retries):
        try:
            response = session.get(url, timeout=25, allow_redirects=True)
            if response.status_code != 429:
                return response
        except requests.RequestException:
            pass
        time.sleep(2 ** attempt)
    return None


def shard_filter(records: list[ProductRecord], shard: int, total: int) -> list[ProductRecord]:
    def bucket(product_id: str) -> int:
        return int(hashlib.sha256(product_id.encode()).hexdigest(), 16) % total
    return [r for r in records if bucket(r.productId) == shard]


def load_seed(path: Path | None) -> list[ProductRecord]:
    if not path or not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("products", data) if isinstance(data, dict) else data
    return [ProductRecord(**{k: v for k, v in row.items() if k in ProductRecord.__dataclass_fields__}) for row in rows]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--shard", type=int, default=0)
    parser.add_argument("--shards", type=int, default=1)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)
    merged: dict[str, ProductRecord] = {}
    source_states: list[dict[str, str]] = []

    for seed in load_seed(args.seed):
        merged.setdefault(seed.productId, seed).merge(seed)

    for url in discovery_urls():
        response = fetch(session, url)
        state = "request_error" if response is None else response_state(response)
        source_states.append({"url": url, "state": state})
        if response is None or state != "ok":
            continue
        for found in extract_links(response.text, url):
            merged.setdefault(found.productId, found).merge(found)

    selected = shard_filter(sorted(merged.values(), key=lambda x: int(x.productId)), args.shard, args.shards)
    if args.limit:
        selected = selected[: args.limit]

    completed: list[ProductRecord] = []
    for record in selected:
        detail_url = record.productUrl or f"{BASE}/vp/products/{record.productId}"
        response = fetch(session, detail_url)
        record.checkedAt = now_iso()
        if response is None:
            record.responseState = "request_error"
        else:
            record.responseState = response_state(response)
            if record.responseState == "ok":
                parse_detail(record, response.text)
        completed.append(record)

    unresolved = [asdict(r) for r in completed if r.responseState != "ok"]
    payload = {
        "seller": {"name": SELLER_NAME, "sellerId": SELLER_ID, "expectedCount": 195},
        "shard": {"index": args.shard, "total": args.shards},
        "generatedAt": now_iso(),
        "sourceStates": source_states,
        "summary": {
            "discoveredCount": len(merged),
            "shardInputCount": len(selected),
            "recoveredCount": sum(r.responseState == "ok" for r in completed),
            "remainingUnresolved": len(unresolved),
        },
        "products": [asdict(r) for r in completed],
        "unresolved": unresolved,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
