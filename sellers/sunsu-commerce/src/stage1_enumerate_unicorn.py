#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus, urljoin

import requests
from bs4 import BeautifulSoup

SELLER_ID = "A01593407"
SELLER_NAME = "순수커머스"
EXPECTED = 195
BASE = "https://www.coupang.com"
UNICORN_RE = re.compile(r"(?:\(주\)\s*유니콘|주식회사\s*유니콘|BOOKFRIENDS|UNICORN|(?<![가-힣])유니콘(?![가-힣]))", re.I)
PRODUCT_RE = re.compile(r"/vp/products/(\d+)")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
}

@dataclass
class Product:
    productId: str
    itemId: str = ""
    vendorItemId: str = ""
    productName: str = ""
    productUrl: str = ""
    publisherManufacturer: str = ""
    responseState: str = "discovered"
    publisherGrade: str = "미확정"
    publisherReason: str = ""
    evidenceUrls: list[str] | None = None
    checkedAt: str = ""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def state(r: requests.Response | None) -> str:
    if r is None:
        return "request_error"
    if r.status_code in (401, 403, 429):
        return "blocked"
    if not r.text.strip():
        return "empty"
    low = r.text.lower()
    if any(x in low for x in ("access denied", "captcha", "비정상적인 접근", "forbidden")):
        return "blocked"
    return "ok" if r.ok else f"http_{r.status_code}"


def get(session: requests.Session, url: str, retries: int = 3) -> requests.Response | None:
    for i in range(retries):
        try:
            r = session.get(url, timeout=25, allow_redirects=True)
            if r.status_code != 429:
                return r
        except requests.RequestException:
            pass
        time.sleep(2 ** i)
    return None


def add_url(store: dict[str, Product], url: str, evidence: str) -> None:
    m = PRODUCT_RE.search(url)
    if not m:
        return
    pid = m.group(1)
    from urllib.parse import urlparse, parse_qs
    q = parse_qs(urlparse(url).query)
    item = (q.get("itemId") or [""])[0]
    vendor = (q.get("vendorItemId") or [""])[0]
    p = store.setdefault(pid, Product(productId=pid, productUrl=url, evidenceUrls=[]))
    if not p.productUrl:
        p.productUrl = url
    if item and not p.itemId:
        p.itemId = item
    if vendor and not p.vendorItemId:
        p.vendorItemId = vendor
    p.evidenceUrls = sorted(set((p.evidenceUrls or []) + [evidence]))


def extract_product_urls(html: str, base_url: str) -> set[str]:
    out: set[str] = set()
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if "/vp/products/" in href:
            out.add(urljoin(BASE, href))
    for m in re.finditer(r"https?://(?:www\.)?coupang\.com/vp/products/\d+[^\"'<> ]*", html):
        out.add(m.group(0).replace("&amp;", "&"))
    for m in PRODUCT_RE.finditer(html):
        out.add(urljoin(BASE, m.group(0)))
    return out


def discovery_sources() -> list[str]:
    queries = [
        f'site:coupang.com/vp/products "{SELLER_NAME}"',
        f'site:coupang.com/vp/products "판매자: {SELLER_NAME}"',
        f'site:coupang.com/vp/products "{SELLER_NAME}" 유니콘',
        f'site:coupang.com/vp/products "{SELLER_NAME}" 퍼즐',
        f'site:coupang.com/vp/products "{SELLER_NAME}" 색칠북',
        f'site:coupang.com/vp/products "{SELLER_NAME}" 스티커',
    ]
    urls = [
        f"https://shop.coupang.com/{SELLER_ID}?locale=ko_KR&platform=p",
        f"https://shop.coupang.com/{SELLER_ID}?locale=ko_KR&platform=m",
    ]
    for q in queries:
        urls.extend([
            f"https://www.google.com/search?q={quote_plus(q)}&num=100",
            f"https://www.bing.com/search?q={quote_plus(q)}&count=50",
            f"https://html.duckduckgo.com/html/?q={quote_plus(q)}",
            f"https://search.naver.com/search.naver?where=web&query={quote_plus(q)}",
        ])
    return urls


def parse_detail(p: Product, html: str) -> None:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.select_one("h1.prod-buy-header__title") or soup.select_one("meta[property='og:title']")
    if title:
        p.productName = (title.get("content", "") if title.name == "meta" else title.get_text(" ", strip=True)).strip()
    text = soup.get_text(" ", strip=True)
    patterns = [
        r"(?:출판사|제조사|제조자\s*\(수입자\))\s*[:：]?\s*([^|]{1,80})",
        r"(?:브랜드)\s*[:：]?\s*([^|]{1,80})",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            p.publisherManufacturer = re.sub(r"\s+", " ", m.group(1)).strip()
            break
    direct = " ".join([p.productName, p.publisherManufacturer, text[:5000]])
    if UNICORN_RE.search(direct):
        p.publisherGrade = "확정"
        p.publisherReason = "쿠팡 상품명 또는 필수표기 직접표기"
    else:
        p.publisherGrade = "미확정"
        p.publisherReason = "쿠팡 직접표기 없음"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--confirmed-out", type=Path, required=True)
    args = ap.parse_args()

    s = requests.Session(); s.headers.update(HEADERS)
    products: dict[str, Product] = {}
    source_log = []

    for url in discovery_sources():
        r = get(s, url)
        st = state(r)
        source_log.append({"url": url, "state": st, "checkedAt": now()})
        if st != "ok" or r is None:
            continue
        for u in extract_product_urls(r.text, url):
            add_url(products, u, url)

    for p in products.values():
        detail = p.productUrl or f"{BASE}/vp/products/{p.productId}"
        r = get(s, detail)
        p.responseState = state(r)
        p.checkedAt = now()
        if p.responseState == "ok" and r is not None:
            parse_detail(p, r.text)

    rows = [asdict(x) for x in sorted(products.values(), key=lambda z: int(z.productId))]
    confirmed = [x for x in rows if x["publisherGrade"] == "확정"]
    payload = {
        "seller": {"name": SELLER_NAME, "sellerId": SELLER_ID, "expectedCount": EXPECTED},
        "summary": {
            "discoveredUniqueProductIds": len(rows),
            "expectedCount": EXPECTED,
            "catalogComplete": len(rows) == EXPECTED,
            "confirmedUnicorn": len(confirmed),
            "blockedOrUnresolved": sum(x["responseState"] != "ok" for x in rows),
        },
        "sourceLog": source_log,
        "products": rows,
        "generatedAt": now(),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.confirmed_out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    args.confirmed_out.write_text(json.dumps(confirmed, ensure_ascii=False, indent=2), encoding="utf-8")

    if len(rows) != EXPECTED:
        raise SystemExit(f"stage1 incomplete: expected {EXPECTED}, discovered {len(rows)}")

if __name__ == "__main__":
    main()
