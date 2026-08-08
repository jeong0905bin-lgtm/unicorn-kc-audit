#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote_plus, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import BrowserContext, Page, Response, sync_playwright

SELLER_ID = "A01593407"
SELLER_NAME = "순수커머스"
EXPECTED = 195
BASE = "https://www.coupang.com"
SHOP_BASE = f"https://shop.coupang.com/{SELLER_ID}"
PRODUCT_RE = re.compile(r"/vp/products/(\d+)")
UNICORN_RE = re.compile(
    r"(?:\(주\)\s*유니콘|주식회사\s*유니콘|BOOKFRIENDS|UNICORN|(?<![가-힣])유니콘(?![가-힣]))",
    re.I,
)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
}


@dataclass
class Product:
    productId: str
    itemId: str = ""
    vendorItemId: str = ""
    productName: str = ""
    productUrl: str = ""
    category: str = ""
    brand: str = ""
    publisherManufacturer: str = ""
    isbn: str = ""
    responseState: str = "discovered"
    publisherGrade: str = "미확정"
    publisherReason: str = ""
    evidenceUrls: list[str] | None = None
    checkedAt: str = ""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def product_url(pid: str, item: str = "", vendor: str = "") -> str:
    url = f"{BASE}/vp/products/{pid}"
    params = []
    if item:
        params.append(f"itemId={item}")
    if vendor:
        params.append(f"vendorItemId={vendor}")
    return url + (("?" + "&".join(params)) if params else "")


def add_product(
    store: dict[str, Product],
    pid: str,
    *,
    url: str = "",
    item_id: str = "",
    vendor_item_id: str = "",
    name: str = "",
    evidence: str = "",
) -> None:
    pid = str(pid or "").strip()
    if not pid.isdigit():
        return
    p = store.setdefault(pid, Product(productId=pid, evidenceUrls=[]))
    if url and not p.productUrl:
        p.productUrl = url
    if item_id and not p.itemId:
        p.itemId = str(item_id)
    if vendor_item_id and not p.vendorItemId:
        p.vendorItemId = str(vendor_item_id)
    if name and not p.productName:
        p.productName = re.sub(r"\s+", " ", str(name)).strip()
    if evidence:
        p.evidenceUrls = sorted(set((p.evidenceUrls or []) + [evidence]))
    if not p.productUrl:
        p.productUrl = product_url(pid, p.itemId, p.vendorItemId)


def add_url(store: dict[str, Product], url: str, evidence: str) -> None:
    m = PRODUCT_RE.search(url or "")
    if not m:
        return
    q = parse_qs(urlparse(url).query)
    add_product(
        store,
        m.group(1),
        url=urljoin(BASE, url),
        item_id=(q.get("itemId") or [""])[0],
        vendor_item_id=(q.get("vendorItemId") or [""])[0],
        evidence=evidence,
    )


def extract_urls(text: str) -> set[str]:
    urls: set[str] = set()
    if not text:
        return urls
    for m in re.finditer(r"https?://(?:www\.)?coupang\.com/vp/products/\d+[^\"'<>\s]*", text):
        urls.add(m.group(0).replace("&amp;", "&"))
    for m in re.finditer(r"(?:href|url)[\"']?\s*[:=]\s*[\"']([^\"']*/vp/products/\d+[^\"']*)", text, re.I):
        urls.add(urljoin(BASE, m.group(1).replace("\\/", "/").replace("&amp;", "&")))
    for m in PRODUCT_RE.finditer(text):
        urls.add(urljoin(BASE, m.group(0)))
    return urls


def scan_json(node: Any, store: dict[str, Product], evidence: str) -> None:
    if isinstance(node, dict):
        pid = node.get("productId") or node.get("productID") or node.get("product_id")
        item = node.get("itemId") or node.get("itemID") or node.get("item_id")
        vendor = node.get("vendorItemId") or node.get("vendorItemID") or node.get("vendor_item_id")
        name = (
            node.get("productName")
            or node.get("title")
            or node.get("name")
            or node.get("displayProductName")
        )
        url = node.get("productUrl") or node.get("url") or node.get("link") or ""
        if pid:
            add_product(
                store,
                str(pid),
                url=urljoin(BASE, str(url)) if url else "",
                item_id=str(item or ""),
                vendor_item_id=str(vendor or ""),
                name=str(name or ""),
                evidence=evidence,
            )
        for value in node.values():
            scan_json(value, store, evidence)
    elif isinstance(node, list):
        for value in node:
            scan_json(value, store, evidence)
    elif isinstance(node, str):
        for url in extract_urls(node):
            add_url(store, url, evidence)


def browser_collect(store: dict[str, Product], source_log: list[dict[str, Any]]) -> None:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        context: BrowserContext = browser.new_context(
            user_agent=HEADERS["User-Agent"],
            locale="ko-KR",
            timezone_id="Asia/Seoul",
            viewport={"width": 1440, "height": 1100},
            extra_http_headers={"Accept-Language": HEADERS["Accept-Language"]},
        )
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = context.new_page()

        def on_response(response: Response) -> None:
            try:
                ctype = (response.headers.get("content-type") or "").lower()
                if not any(x in ctype for x in ("json", "javascript", "text", "html")):
                    return
                body = response.text()
                if len(body) > 8_000_000:
                    return
                for url in extract_urls(body):
                    add_url(store, url, response.url)
                if "json" in ctype:
                    try:
                        scan_json(json.loads(body), store, response.url)
                    except json.JSONDecodeError:
                        pass
            except Exception:
                return

        page.on("response", on_response)

        targets = [
            f"{SHOP_BASE}?locale=ko_KR&platform=p",
            f"{SHOP_BASE}?locale=ko_KR&platform=m",
            f"{SHOP_BASE}?locale=ko_KR&platform=p&sorter=BEST_SELLING",
            f"{SHOP_BASE}?locale=ko_KR&platform=p&sorter=NEWEST",
        ]
        # Search pages are used as secondary discovery only; seller verification happens later.
        for page_no in range(1, 11):
            targets.append(f"https://www.coupang.com/np/search?q={quote_plus(SELLER_NAME)}&page={page_no}")
        for query in [
            f'site:coupang.com/vp/products "{SELLER_NAME}"',
            f'site:coupang.com/vp/products "판매자 {SELLER_NAME}"',
            f'site:coupang.com/vp/products "{SELLER_NAME}" 퍼즐',
            f'site:coupang.com/vp/products "{SELLER_NAME}" 색칠북',
            f'site:coupang.com/vp/products "{SELLER_NAME}" 스티커',
        ]:
            targets.extend(
                [
                    f"https://www.google.com/search?q={quote_plus(query)}&num=100",
                    f"https://www.bing.com/search?q={quote_plus(query)}&count=50",
                    f"https://search.naver.com/search.naver?where=web&query={quote_plus(query)}",
                ]
            )

        for target in targets:
            before = len(store)
            status = "ok"
            error = ""
            try:
                page.goto(target, wait_until="domcontentloaded", timeout=45_000)
                page.wait_for_timeout(2_000)
                for _ in range(24):
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(700)
                    for selector in [
                        "button:has-text('더보기')",
                        "button:has-text('MORE')",
                        "a:has-text('다음')",
                    ]:
                        try:
                            loc = page.locator(selector).first
                            if loc.is_visible(timeout=100):
                                loc.click(timeout=500)
                                page.wait_for_timeout(700)
                        except Exception:
                            pass
                html = page.content()
                for url in extract_urls(html):
                    add_url(store, url, target)
                for href in page.locator("a[href*='/vp/products/']").evaluate_all("els => els.map(e => e.href)"):
                    add_url(store, href, target)
            except Exception as exc:
                status = "error"
                error = f"{type(exc).__name__}: {exc}"[:500]
            source_log.append(
                {
                    "url": target,
                    "state": status,
                    "newProductIds": len(store) - before,
                    "totalProductIds": len(store),
                    "error": error,
                    "checkedAt": now(),
                }
            )
            if len(store) >= EXPECTED:
                break

        # Verify candidate pages really belong to this seller and capture identifiers/title.
        candidate_ids = list(store)
        for index, pid in enumerate(candidate_ids):
            p = store[pid]
            url = p.productUrl or product_url(pid, p.itemId, p.vendorItemId)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=35_000)
                page.wait_for_timeout(900)
                html = page.content()
                text = page.locator("body").inner_text(timeout=5_000)
                seller_match = SELLER_NAME in text or SELLER_NAME in html
                # Search-result candidates not confirmed as the seller are removed.
                if not seller_match and not any(SHOP_BASE in x for x in (p.evidenceUrls or [])):
                    del store[pid]
                    continue
                parse_detail(p, html, text)
                p.responseState = "ok"
                p.checkedAt = now()
            except Exception as exc:
                p.responseState = f"browser_error:{type(exc).__name__}"
                p.checkedAt = now()
            if index and index % 25 == 0:
                print({"verified": index, "remaining": len(store)}, flush=True)
        browser.close()


def parse_detail(p: Product, html: str, text: str = "") -> None:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.select_one("h1.prod-buy-header__title") or soup.select_one("meta[property='og:title']")
    if title:
        p.productName = (
            title.get("content", "") if title.name == "meta" else title.get_text(" ", strip=True)
        ).strip()
    if not text:
        text = soup.get_text(" ", strip=True)
    for label, attr in [
        (r"브랜드\s*[:：]?\s*([^|\n]{1,100})", "brand"),
        (r"(?:출판사|제조사|제조자\s*\(수입자\))\s*[:：]?\s*([^|\n]{1,120})", "publisherManufacturer"),
        (r"ISBN\s*[:：]?\s*([0-9Xx-]{10,20})", "isbn"),
    ]:
        match = re.search(label, text, re.I)
        if match:
            setattr(p, attr, re.sub(r"\s+", " ", match.group(1)).strip())
    direct = " ".join([p.productName, p.brand, p.publisherManufacturer, text[:15_000]])
    if UNICORN_RE.search(direct):
        p.publisherGrade = "확정"
        p.publisherReason = "쿠팡 상품명 또는 상세 필수표기 직접표기"
    else:
        keywords = ["판퍼즐", "대판퍼즐", "스티커", "색칠북", "워터색칠북", "컬렉션북"]
        if any(k in p.productName for k in keywords):
            p.publisherGrade = "후보"
            p.publisherReason = "유니콘 가능 상품군; ISBN/공식 도서정보 교차검증 필요"
        else:
            p.publisherGrade = "미확정"
            p.publisherReason = "직접 표기 없음"


def direct_http_collect(store: dict[str, Product], source_log: list[dict[str, Any]]) -> None:
    session = requests.Session()
    session.headers.update(HEADERS)
    sources = [
        f"{SHOP_BASE}?locale=ko_KR&platform=p",
        f"{SHOP_BASE}?locale=ko_KR&platform=m",
    ]
    for q in [
        f'site:coupang.com/vp/products "{SELLER_NAME}"',
        f'site:coupang.com/vp/products "{SELLER_NAME}" 퍼즐',
        f'site:coupang.com/vp/products "{SELLER_NAME}" 색칠북',
    ]:
        sources.extend(
            [
                f"https://www.google.com/search?q={quote_plus(q)}&num=100",
                f"https://www.bing.com/search?q={quote_plus(q)}&count=50",
                f"https://html.duckduckgo.com/html/?q={quote_plus(q)}",
            ]
        )
    for url in sources:
        before = len(store)
        try:
            r = session.get(url, timeout=30, allow_redirects=True)
            state = "ok" if r.ok and r.text.strip() else f"http_{r.status_code}"
            if r.ok:
                for found in extract_urls(r.text):
                    add_url(store, found, url)
        except requests.RequestException as exc:
            state = f"request_error:{type(exc).__name__}"
        source_log.append(
            {
                "url": url,
                "state": state,
                "newProductIds": len(store) - before,
                "totalProductIds": len(store),
                "checkedAt": now(),
            }
        )
        time.sleep(0.25)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--confirmed-out", type=Path, required=True)
    args = parser.parse_args()

    products: dict[str, Product] = {}
    source_log: list[dict[str, Any]] = []
    direct_http_collect(products, source_log)
    browser_collect(products, source_log)

    rows = [asdict(x) for x in sorted(products.values(), key=lambda p: int(p.productId))]
    candidates = [x for x in rows if x["publisherGrade"] in {"확정", "후보"}]
    payload = {
        "seller": {"name": SELLER_NAME, "sellerId": SELLER_ID, "expectedCount": EXPECTED},
        "summary": {
            "discoveredUniqueProductIds": len(rows),
            "expectedCount": EXPECTED,
            "catalogComplete": len(rows) == EXPECTED,
            "confirmedUnicorn": sum(x["publisherGrade"] == "확정" for x in rows),
            "publisherCandidates": len(candidates),
            "blockedOrUnresolved": sum(x["responseState"] != "ok" for x in rows),
        },
        "sourceLog": source_log,
        "products": rows,
        "generatedAt": now(),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.confirmed_out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    args.confirmed_out.write_text(json.dumps(candidates, ensure_ascii=False, indent=2), encoding="utf-8")
    print(payload["summary"], flush=True)
    if len(rows) != EXPECTED:
        raise SystemExit(f"stage1 incomplete: expected {EXPECTED}, discovered {len(rows)}")


if __name__ == "__main__":
    main()
