#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

from playwright.sync_api import Response, sync_playwright

BASE = "https://www.coupang.com"
SELLER = "순수커머스"
EXPECTED = 195
SEEDS = ["1329308694", "274824520", "6209404889", "6209404899"]
PRODUCT_RE = re.compile(r"/vp/products/(\d+)")
VENDOR_RE = re.compile(r"/vp/vendors/([A-Za-z0-9_-]+)")
UNICORN_RE = re.compile(r"(?:\(주\)\s*유니콘|주식회사\s*유니콘|BOOKFRIENDS|UNICORN|(?<![가-힣])유니콘(?![가-힣]))", re.I)


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
    publisherGrade: str = "미확정"
    publisherReason: str = ""
    evidenceUrls: list[str] | None = None
    checkedAt: str = ""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def add_product(store: dict[str, Product], pid: Any, evidence: str, *, url: str = "", item: Any = "", vendor_item: Any = "", name: Any = "") -> None:
    pid = str(pid or "").strip()
    if not pid.isdigit():
        return
    p = store.setdefault(pid, Product(productId=pid, evidenceUrls=[]))
    p.productUrl = p.productUrl or url or f"{BASE}/vp/products/{pid}"
    p.itemId = p.itemId or str(item or "")
    p.vendorItemId = p.vendorItemId or str(vendor_item or "")
    p.productName = p.productName or re.sub(r"\s+", " ", str(name or "")).strip()
    p.evidenceUrls = sorted(set((p.evidenceUrls or []) + [evidence]))


def add_url(store: dict[str, Product], raw_url: str, evidence: str) -> None:
    url = urljoin(BASE, (raw_url or "").replace("\\/", "/").replace("&amp;", "&"))
    m = PRODUCT_RE.search(url)
    if not m:
        return
    q = parse_qs(urlparse(url).query)
    add_product(store, m.group(1), evidence, url=url, item=(q.get("itemId") or [""])[0], vendor_item=(q.get("vendorItemId") or [""])[0])


def scan(node: Any, store: dict[str, Product], evidence: str) -> None:
    if isinstance(node, dict):
        pid = node.get("productId") or node.get("productID") or node.get("product_id")
        item = node.get("itemId") or node.get("itemID") or node.get("item_id")
        vendor_item = node.get("vendorItemId") or node.get("vendorItemID") or node.get("vendor_item_id")
        name = node.get("productName") or node.get("displayProductName") or node.get("title") or node.get("name")
        url = node.get("productUrl") or node.get("url") or node.get("link") or ""
        if pid:
            add_product(store, pid, evidence, url=urljoin(BASE, str(url)) if url else "", item=item, vendor_item=vendor_item, name=name)
        for value in node.values():
            scan(value, store, evidence)
    elif isinstance(node, list):
        for value in node:
            scan(value, store, evidence)
    elif isinstance(node, str):
        for m in re.finditer(r"(?:https?://(?:www\.)?coupang\.com)?/vp/products/\d+[^\"'<>\s]*", node):
            add_url(store, m.group(0), evidence)


def page_variants(url: str) -> list[str]:
    parsed = urlparse(url)
    q = parse_qs(parsed.query)
    variants = [url]
    page_keys = [k for k in q if k.lower() in {"page", "pageno", "page_num", "pageindex", "offset"}]
    if page_keys:
        for n in range(1, 31):
            nq = {k: list(v) for k, v in q.items()}
            for key in page_keys:
                nq[key] = [str((n - 1) * 20 if key.lower() == "offset" else n)]
            variants.append(urlunparse(parsed._replace(query=urlencode(nq, doseq=True))))
    return list(dict.fromkeys(variants))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--confirmed-out", type=Path, required=True)
    args = ap.parse_args()

    store: dict[str, Product] = {}
    source_log: list[dict[str, Any]] = []
    vendor_ids: set[str] = set()
    catalog_api_urls: set[str] = set()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"])
        context = browser.new_context(
            locale="ko-KR",
            timezone_id="Asia/Seoul",
            viewport={"width": 1440, "height": 1200},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/127 Safari/537.36",
        )
        context.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page = context.new_page()

        def on_response(response: Response) -> None:
            try:
                ctype = (response.headers.get("content-type") or "").lower()
                if not any(x in ctype for x in ("json", "javascript", "html", "text")):
                    return
                body = response.text()
                if len(body) > 15_000_000:
                    return
                before = len(store)
                try:
                    if "json" in ctype or body.lstrip().startswith(("{", "[")):
                        scan(json.loads(body), store, response.url)
                except Exception:
                    scan(body, store, response.url)
                for m in VENDOR_RE.finditer(response.url + " " + body):
                    vendor_ids.add(m.group(1))
                low = response.url.lower()
                if any(k in low for k in ("vendor", "seller", "store", "shop", "product")) and len(store) > before:
                    catalog_api_urls.add(response.url)
            except Exception:
                pass

        page.on("response", on_response)

        for pid in SEEDS:
            target = f"{BASE}/vp/products/{pid}"
            try:
                page.goto(target, wait_until="domcontentloaded", timeout=60_000)
                page.wait_for_timeout(2500)
                html = page.content()
                body = page.locator("body").inner_text(timeout=5000)
                add_product(store, pid, target, url=target)
                scan(html, store, target)
                for href in page.locator("a[href]").evaluate_all("els=>els.map(e=>e.href)"):
                    add_url(store, href, target)
                    vm = VENDOR_RE.search(href)
                    if vm and (SELLER in body or SELLER in html):
                        vendor_ids.add(vm.group(1))
                source_log.append({"type": "seed", "url": target, "sellerSeen": SELLER in body or SELLER in html, "vendorIds": sorted(vendor_ids), "count": len(store), "checkedAt": now()})
            except Exception as exc:
                source_log.append({"type": "seed", "url": target, "error": f"{type(exc).__name__}: {exc}"[:600], "count": len(store), "checkedAt": now()})

        # Open every discovered vendor page and aggressively paginate/scroll.
        for vendor_id in sorted(vendor_ids):
            targets = [
                f"{BASE}/vp/vendors/{vendor_id}",
                f"{BASE}/vp/vendors/{vendor_id}?page=1",
                f"{BASE}/vp/vendors/{vendor_id}?sortType=NEWEST&page=1",
                f"{BASE}/vp/vendors/{vendor_id}?sortType=BEST_SELLING&page=1",
            ]
            for target in targets:
                before = len(store)
                try:
                    page.goto(target, wait_until="domcontentloaded", timeout=60_000)
                    page.wait_for_timeout(2000)
                    for _ in range(120):
                        page.mouse.wheel(0, 1800)
                        page.wait_for_timeout(250)
                        for sel in ["button:has-text('더보기')", "button:has-text('MORE')", "a:has-text('다음')"]:
                            try:
                                el = page.locator(sel).first
                                if el.is_visible(timeout=50):
                                    el.click(timeout=400)
                            except Exception:
                                pass
                    scan(page.content(), store, target)
                    for href in page.locator("a[href*='/vp/products/']").evaluate_all("els=>els.map(e=>e.href)"):
                        add_url(store, href, target)
                    source_log.append({"type": "vendor-page", "url": target, "new": len(store) - before, "count": len(store), "checkedAt": now()})
                except Exception as exc:
                    source_log.append({"type": "vendor-page", "url": target, "error": f"{type(exc).__name__}: {exc}"[:600], "count": len(store), "checkedAt": now()})

        # Replay captured catalog endpoints with page/offset variants in the browser context.
        for api_url in list(catalog_api_urls):
            for variant in page_variants(api_url):
                if len(store) >= EXPECTED:
                    break
                before = len(store)
                try:
                    resp = context.request.get(variant, timeout=35_000, headers={"referer": f"{BASE}/"})
                    text = resp.text()
                    try:
                        scan(json.loads(text), store, variant)
                    except Exception:
                        scan(text, store, variant)
                    source_log.append({"type": "api-replay", "url": variant, "status": resp.status, "new": len(store) - before, "count": len(store), "checkedAt": now()})
                except Exception as exc:
                    source_log.append({"type": "api-replay", "url": variant, "error": f"{type(exc).__name__}: {exc}"[:500], "count": len(store), "checkedAt": now()})

        # Detail verification/classification only after catalog collection.
        for i, (pid, product) in enumerate(list(store.items())):
            try:
                page.goto(product.productUrl or f"{BASE}/vp/products/{pid}", wait_until="domcontentloaded", timeout=30_000)
                page.wait_for_timeout(500)
                body = page.locator("body").inner_text(timeout=4000)
                html = page.content()
                try:
                    product.productName = page.locator("h1").first.inner_text(timeout=1200).strip() or product.productName
                except Exception:
                    pass
                for pattern, attr in [
                    (r"브랜드\s*[:：]?\s*([^\n|]{1,100})", "brand"),
                    (r"(?:출판사|제조사|제조자\s*\(수입자\))\s*[:：]?\s*([^\n|]{1,120})", "publisherManufacturer"),
                    (r"ISBN\s*[:：]?\s*([0-9Xx-]{10,20})", "isbn"),
                ]:
                    m = re.search(pattern, body, re.I)
                    if m:
                        setattr(product, attr, re.sub(r"\s+", " ", m.group(1)).strip())
                direct = " ".join([product.productName, product.brand, product.publisherManufacturer, body[:15000]])
                if UNICORN_RE.search(direct):
                    product.publisherGrade = "확정"
                    product.publisherReason = "쿠팡 상품명 또는 필수표기 직접표기"
                elif any(k in product.productName for k in ("퍼즐", "스티커", "색칠북", "컬렉션북", "워터색칠북")):
                    product.publisherGrade = "후보"
                    product.publisherReason = "ISBN/공식 도서정보 교차검증 필요"
                product.checkedAt = now()
                # Remove obvious non-seller search leakage only when seller name is explicitly another seller.
                if SELLER not in body and SELLER not in html and not any("/vp/vendors/" in e for e in (product.evidenceUrls or [])):
                    product.publisherReason = (product.publisherReason + "; 판매자 재검증 필요").strip("; ")
            except Exception:
                product.checkedAt = now()
            if i and i % 25 == 0:
                print({"verified": i, "collected": len(store)}, flush=True)

        browser.close()

    rows = [asdict(v) for v in sorted(store.values(), key=lambda x: int(x.productId))]
    candidates = [r for r in rows if r["publisherGrade"] in ("확정", "후보")]
    payload = {
        "seller": {"name": SELLER, "expectedCount": EXPECTED},
        "summary": {
            "discoveredUniqueProductIds": len(rows),
            "expectedCount": EXPECTED,
            "catalogComplete": len(rows) == EXPECTED,
            "publisherCandidates": len(candidates),
            "vendorIds": sorted(vendor_ids),
            "catalogApiCount": len(catalog_api_urls),
        },
        "sourceLog": source_log,
        "products": rows,
        "generatedAt": now(),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    args.confirmed_out.write_text(json.dumps(candidates, ensure_ascii=False, indent=2), encoding="utf-8")
    if len(rows) != EXPECTED:
        raise SystemExit(f"stage1 incomplete: expected {EXPECTED}, discovered {len(rows)}; vendors={sorted(vendor_ids)}; api={len(catalog_api_urls)}")


if __name__ == "__main__":
    main()
