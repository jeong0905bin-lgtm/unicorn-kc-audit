#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

from playwright.sync_api import sync_playwright

SELLER_ID = "A00214628"
BASE = f"https://shop.coupang.com/{SELLER_ID}"
PRODUCT_RE = re.compile(r"/vp/products/(\d+)")
URL_RE = re.compile(r"(?:https?://(?:www\.)?coupang\.com)?/vp/products/\d+[^\"'<>\s]*", re.I)
TRIPLE_RE = re.compile(
    r'"productId"\s*:\s*"?(\d+)"?.{0,900}?"itemId"\s*:\s*"?(\d+)"?.{0,900}?"vendorItemId"\s*:\s*"?(\d+)"?',
    re.I | re.S,
)
BLOCK_TERMS = ("access denied", "captcha", "보안 확인", "비정상적인 접근", "서버에서 오류가 발생")
PROFILES = [
    (
        "desktop",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/149.0.0.0 Safari/537.36",
        {"width": 1440, "height": 1200},
        False,
    ),
    (
        "mobile",
        "Mozilla/5.0 (Linux; Android 15; SM-S928N) AppleWebKit/537.36 Chrome/149.0.0.0 Mobile Safari/537.36",
        {"width": 430, "height": 932},
        True,
    ),
]
URL_VARIANTS = [
    f"{BASE}?source=brandstore_sdp_atf&platform=p",
    f"{BASE}?platform=p",
    BASE,
    f"{BASE}?sortType=SALE&platform=p",
    f"{BASE}?sortType=NEW&platform=p",
    f"{BASE}?sortType=PRICE_ASC&platform=p",
    f"{BASE}?sortType=PRICE_DESC&platform=p",
    f"{BASE}?ocid=1208642&checkBatchDelivery=true&platform=p",
]


def save(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def canonical(raw: str) -> str | None:
    if not raw:
        return None
    raw = html.unescape(raw).replace("\\/", "/")
    raw = urljoin("https://www.coupang.com", raw)
    parsed = urlparse(raw)
    match = PRODUCT_RE.search(parsed.path)
    if not match:
        return None
    query = parse_qs(parsed.query)
    parts = []
    if query.get("itemId"):
        parts.append("itemId=" + query["itemId"][0])
    if query.get("vendorItemId"):
        parts.append("vendorItemId=" + query["vendorItemId"][0])
    return f"https://www.coupang.com/vp/products/{match.group(1)}" + (("?" + "&".join(parts)) if parts else "")


def ids(url: str) -> tuple[str, str, str]:
    parsed = urlparse(url)
    match = PRODUCT_RE.search(parsed.path)
    query = parse_qs(parsed.query)
    return (
        match.group(1) if match else "",
        (query.get("itemId") or [""])[0],
        (query.get("vendorItemId") or [""])[0],
    )


def normalize_image(raw: str) -> str:
    if not raw:
        return ""
    raw = html.unescape(raw).replace("\\/", "/")
    if raw.startswith("//"):
        raw = "https:" + raw
    return raw if "coupangcdn.com" in raw else ""


def add(found: dict, raw_url: str, name: str = "", image: str = "") -> None:
    url = canonical(raw_url)
    if not url:
        return
    key = ids(url)
    candidate = {
        "productId": key[0],
        "itemId": key[1],
        "vendorItemId": key[2],
        "productUrl": url,
        "sourceName": re.sub(r"\s+", " ", name or "").strip()[:500],
        "mainImageUrl": normalize_image(image),
    }
    old = found.get(key)
    score = 100 * bool(candidate["vendorItemId"]) + 10 * bool(candidate["sourceName"]) + bool(candidate["mainImageUrl"])
    old_score = -1 if old is None else 100 * bool(old.get("vendorItemId")) + 10 * bool(old.get("sourceName")) + bool(old.get("mainImageUrl"))
    if old is None or score > old_score:
        found[key] = candidate


def add_text(text: str, found: dict) -> None:
    if not text:
        return
    clean = html.unescape(text).replace("\\/", "/")
    for match in URL_RE.finditer(clean):
        add(found, match.group(0))
    for product_id, item_id, vendor_item_id in TRIPLE_RE.findall(clean):
        add(found, f"https://www.coupang.com/vp/products/{product_id}?itemId={item_id}&vendorItemId={vendor_item_id}")


def scrape_dom(page, found: dict) -> None:
    try:
        cards = page.locator('a[href*="/vp/products/"]').evaluate_all(
            """els => els.map(a => ({
                href: a.href || a.getAttribute('href') || '',
                text: (a.innerText || a.textContent || '').trim(),
                image: (() => { const img=a.querySelector('img'); return img ? (img.currentSrc || img.src || img.getAttribute('data-src') || img.getAttribute('data-original') || '') : ''; })()
            }))"""
        )
        for card in cards:
            add(found, card.get("href", ""), card.get("text", ""), card.get("image", ""))
    except Exception:
        pass
    try:
        add_text(page.content(), found)
    except Exception:
        pass


def clear_context(context, page) -> None:
    context.clear_cookies()
    try:
        cdp = context.new_cdp_session(page)
        cdp.send("Network.enable")
        cdp.send("Network.clearBrowserCache")
        cdp.send("Network.clearBrowserCookies")
        for origin in ("https://shop.coupang.com", "https://www.coupang.com", "https://m.coupang.com"):
            try:
                cdp.send("Storage.clearDataForOrigin", {"origin": origin, "storageTypes": "all"})
            except Exception:
                pass
        cdp.detach()
    except Exception:
        pass


def attempt(browser, url: str, profile, attempt_no: int, max_scrolls: int, diagnostics: Path) -> tuple[dict, dict]:
    name, ua, viewport, mobile = profile
    found: dict = {}
    context = browser.new_context(
        user_agent=ua,
        viewport=viewport,
        locale="ko-KR",
        timezone_id="Asia/Seoul",
        is_mobile=mobile,
        has_touch=mobile,
        service_workers="block",
        extra_http_headers={
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.7",
            "Cache-Control": "no-cache, no-store, max-age=0",
            "Pragma": "no-cache",
        },
    )
    context.set_default_timeout(18000)
    page = context.new_page()
    clear_context(context, page)
    network_count = 0

    def on_response(response):
        nonlocal network_count
        try:
            ctype = (response.headers.get("content-type") or "").lower()
            if any(x in ctype for x in ("json", "javascript", "html", "text")):
                body = response.text()
                if len(body) <= 15_000_000:
                    add_text(body, found)
                    network_count += 1
        except Exception:
            pass

    page.on("response", on_response)
    diag = {"attempt": attempt_no, "profile": name, "url": url, "found": 0, "error": "", "blockedTerms": [], "networkBodies": 0}
    try:
        bust = ("&" if "?" in url else "?") + f"_fresh={int(time.time() * 1000)}-{attempt_no}"
        page.goto(url + bust, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(1800)
        stable = 0
        previous = -1
        for scroll_no in range(max_scrolls):
            scrape_dom(page, found)
            stable = stable + 1 if len(found) == previous else 0
            previous = len(found)
            if scroll_no % 10 == 0:
                print(f"catalog attempt={attempt_no} profile={name} scroll={scroll_no} products={len(found)} stable={stable}", flush=True)
            if len(found) >= 2500 or (stable >= 30 and len(found) >= 10):
                break
            try:
                page.evaluate("window.scrollTo(0, Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)); window.dispatchEvent(new Event('scroll')); window.dispatchEvent(new Event('resize')); ")
                page.mouse.wheel(0, 12000)
            except Exception:
                pass
            page.wait_for_timeout(700)
        scrape_dom(page, found)
        try:
            body = page.locator("body").inner_text(timeout=5000)
        except Exception:
            body = ""
        low = body.lower()
        diag["blockedTerms"] = [term for term in BLOCK_TERMS if term in low]
        diagnostics.mkdir(parents=True, exist_ok=True)
        (diagnostics / f"attempt-{attempt_no}-{name}.html").write_text(page.content(), encoding="utf-8", errors="ignore")
        page.screenshot(path=str(diagnostics / f"attempt-{attempt_no}-{name}.png"), full_page=False)
    except Exception as exc:
        diag["error"] = f"{type(exc).__name__}: {exc}"[:1000]
    finally:
        diag["found"] = len(found)
        diag["networkBodies"] = network_count
        context.close()
    return found, diag


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--diagnostics", type=Path, required=True)
    parser.add_argument("--worker-index", type=int, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-scrolls", type=int, default=260)
    parser.add_argument("--cycles", type=int, default=2)
    args = parser.parse_args()

    tasks = []
    attempt_no = 0
    for _ in range(max(1, args.cycles)):
        for profile in PROFILES:
            for url in URL_VARIANTS:
                attempt_no += 1
                tasks.append((attempt_no, profile, url))
    tasks = [task for index, task in enumerate(tasks) if index % args.workers == args.worker_index]

    combined: dict = {}
    attempts = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--disable-dev-shm-usage", "--no-sandbox", "--disable-application-cache"])
        try:
            for attempt_no, profile, url in tasks:
                found, diag = attempt(browser, url, profile, attempt_no, args.max_scrolls, args.diagnostics)
                for key, product in found.items():
                    old = combined.get(key)
                    score = 100 * bool(product.get("vendorItemId")) + 10 * bool(product.get("sourceName")) + bool(product.get("mainImageUrl"))
                    old_score = -1 if old is None else 100 * bool(old.get("vendorItemId")) + 10 * bool(old.get("sourceName")) + bool(old.get("mainImageUrl"))
                    if old is None or score > old_score:
                        combined[key] = product
                attempts.append(diag)
                save(args.output.with_suffix(".checkpoint.json"), {
                    "sellerId": SELLER_ID,
                    "workerIndex": args.worker_index,
                    "count": len(combined),
                    "attempts": attempts,
                    "products": list(combined.values()),
                })
        finally:
            browser.close()

    products = sorted(combined.values(), key=lambda p: (int(p.get("productId") or 0), p.get("itemId", ""), p.get("vendorItemId", "")))
    result = {
        "sellerId": SELLER_ID,
        "workerIndex": args.worker_index,
        "workers": args.workers,
        "count": len(products),
        "attempts": attempts,
        "products": products,
    }
    save(args.output, result)
    save(args.diagnostics / "summary.json", result)
    print(json.dumps({"worker": args.worker_index, "catalogCount": len(products)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
