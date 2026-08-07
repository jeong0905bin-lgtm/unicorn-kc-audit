#!/usr/bin/env python3
from __future__ import annotations

import html
import json
import os
import re
import time
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

import requests
from playwright.sync_api import sync_playwright

SELLER_URL = "https://shop.coupang.com/A00214628?source=brandstore_sdp_atf&ocid=1208642&checkBatchDelivery=true&pid=8411161016&viid=91335726263&platform=p&brandId=0&btcEnableForce=false"
PRODUCT_URLS = [
    "https://www.coupang.com/vp/products/8411161016?itemId=27355912643&vendorItemId=91335726263",
    "https://m.coupang.com/vm/products/8411161016?itemId=27355912643&vendorItemId=91335726263",
]
BLOCK_TERMS = ("access denied", "captcha", "보안 확인", "비정상적인 접근", "접근 불가")
PUBLISHER_RE = re.compile(r"저자\s*[,·/ㆍ]?\s*출판사\s*[:：]?\s*([^\n|<>]{1,120})", re.I)
PRODUCT_RE = re.compile(r'(?:"productId"\s*:\s*"?(\d+)"?|/v[pm]/products/(\d+))', re.I)
CATEGORY_RE = re.compile(r'(?:category(?:Id)?["=:\s]+|/A00214628/)(\d{4,9})', re.I)


def bust(url: str, attempt: int) -> str:
    parts = urlsplit(url)
    query = dict(parse_qs(parts.query, keep_blank_values=True))
    query = {k: v[-1] if isinstance(v, list) else v for k, v in query.items()}
    query["_fresh"] = f"{int(time.time() * 1000)}-{attempt}"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def publisher_values(text: str) -> list[str]:
    values = []
    for match in PUBLISHER_RE.finditer(text or ""):
        value = re.sub(r"\s+", " ", html.unescape(match.group(1))).strip()[:120]
        if value and value not in values:
            values.append(value)
    return values


def ids(text: str) -> tuple[list[str], list[str]]:
    products = set()
    for match in PRODUCT_RE.finditer(text or ""):
        products.add(match.group(1) or match.group(2))
    categories = set(CATEGORY_RE.findall(text or ""))
    return sorted(products, key=int), sorted(categories, key=int)


def request_probe(label: str, url: str, attempt: int) -> dict:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/149.0.0.0 Safari/537.36",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.7",
        "Cache-Control": "no-cache, no-store, max-age=0",
        "Pragma": "no-cache",
    }
    try:
        response = requests.get(bust(url, attempt), headers=headers, timeout=25)
        response.encoding = response.apparent_encoding or "utf-8"
        text = response.text
        products, categories = ids(text)
        return {
            "label": label,
            "status": response.status_code,
            "finalUrl": response.url,
            "length": len(response.content),
            "blockedTerms": [term for term in BLOCK_TERMS if term in text.lower()],
            "publisherEvidence": publisher_values(text),
            "productIds": products,
            "categoryIds": categories,
        }
    except Exception as exc:
        return {"label": label, "error": f"{type(exc).__name__}: {exc}"[:1000]}


def clear_context(context, page) -> list[str]:
    notes = []
    try:
        context.clear_cookies()
        notes.append("cookies=cleared")
    except Exception as exc:
        notes.append(f"cookies={type(exc).__name__}")
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
        notes.append("cache_storage=cleared")
    except Exception as exc:
        notes.append(f"cdp={type(exc).__name__}")
    return notes


def browser_probe(browser, label: str, url: str, attempt: int, mobile: bool, out: Path) -> dict:
    ua = (
        "Mozilla/5.0 (Linux; Android 15; SM-S928N) AppleWebKit/537.36 Chrome/149.0.0.0 Mobile Safari/537.36"
        if mobile else
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/149.0.0.0 Safari/537.36"
    )
    context = browser.new_context(
        user_agent=ua,
        locale="ko-KR",
        timezone_id="Asia/Seoul",
        viewport={"width": 430, "height": 932} if mobile else {"width": 1440, "height": 1200},
        is_mobile=mobile,
        has_touch=mobile,
        service_workers="block",
        extra_http_headers={
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.7",
            "Cache-Control": "no-cache, no-store, max-age=0",
            "Pragma": "no-cache",
        },
    )
    context.set_default_timeout(15000)
    page = context.new_page()
    notes = clear_context(context, page)
    responses, bodies, failures = [], [], []

    def on_response(response):
        try:
            ctype = (response.headers.get("content-type") or "").lower()
            responses.append({"status": response.status, "url": response.url, "contentType": ctype[:120]})
            if any(kind in ctype for kind in ("json", "html", "text", "javascript")):
                body = response.text()
                if len(body) <= 15_000_000 and any(token in body for token in ("productId", "vendorItemId", "저자", "출판사", "categoryId")):
                    bodies.append({"status": response.status, "url": response.url, "body": body[:1_500_000]})
        except Exception:
            pass

    page.on("response", on_response)
    page.on("requestfailed", lambda req: failures.append({"url": req.url, "failure": str(req.failure)}))
    goto_error = ""
    try:
        try:
            page.goto(bust(url, attempt), wait_until="domcontentloaded", timeout=45000)
        except Exception as exc:
            goto_error = f"{type(exc).__name__}: {exc}"[:1000]
        page.wait_for_timeout(2200)
        for _ in range(8 if label == "seller" else 2):
            try:
                page.evaluate("window.scrollTo(0, Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)); window.dispatchEvent(new Event('scroll')); window.dispatchEvent(new Event('resize'))")
                page.mouse.wheel(0, 10000)
            except Exception:
                pass
            page.wait_for_timeout(500)
        try:
            body = page.locator("body").inner_text(timeout=5000)
        except Exception:
            body = ""
        try:
            content = page.content()
        except Exception:
            content = ""
        combined = body + "\n" + content + "\n" + "\n".join(x["body"] for x in bodies)
        products, categories = ids(combined)
        result = {
            "label": label,
            "finalUrl": page.url,
            "title": page.title(),
            "gotoError": goto_error,
            "clearNotes": notes,
            "bodyLength": len(body),
            "htmlLength": len(content),
            "blockedTerms": [term for term in BLOCK_TERMS if term in combined.lower()],
            "publisherEvidence": publisher_values(combined),
            "productIds": products,
            "categoryIds": categories,
            "responseStatusCounts": dict(Counter(str(x["status"]) for x in responses)),
            "responses": responses[:1000],
            "requestFailures": failures[:200],
            "capturedBodies": [{"status": x["status"], "url": x["url"], "length": len(x["body"])} for x in bodies],
        }
        stem = f"{attempt:02d}-{label}"
        (out / f"{stem}.html").write_text(content, encoding="utf-8", errors="ignore")
        (out / f"{stem}.txt").write_text(body, encoding="utf-8", errors="ignore")
        for index, item in enumerate(bodies[:20], 1):
            (out / f"{stem}-body-{index:02d}.txt").write_text(item["body"], encoding="utf-8", errors="ignore")
        page.screenshot(path=str(out / f"{stem}.png"), full_page=False)
        return result
    finally:
        context.close()


def main() -> None:
    out = Path(os.environ.get("PROBE_OUTPUT", "probe"))
    out.mkdir(parents=True, exist_ok=True)
    browser_name = os.environ.get("BROWSER_NAME", "chromium")
    runner_label = os.environ.get("RUNNER_LABEL", "unknown")
    result = {
        "runner": runner_label,
        "browser": browser_name,
        "requests": [request_probe("seller", SELLER_URL, 1), request_probe("product", PRODUCT_URLS[0], 2)],
        "browserAttempts": [],
    }
    with sync_playwright() as pw:
        browser_type = getattr(pw, browser_name)
        args = ["--disable-dev-shm-usage", "--no-sandbox", "--disable-application-cache"] if browser_name == "chromium" else []
        browser = browser_type.launch(headless=True, args=args)
        try:
            result["browserAttempts"].append(browser_probe(browser, "seller", SELLER_URL, 10, False, out))
            result["browserAttempts"].append(browser_probe(browser, "product-desktop", PRODUCT_URLS[0], 11, False, out))
            result["browserAttempts"].append(browser_probe(browser, "product-mobile", PRODUCT_URLS[1], 12, True, out))
        finally:
            browser.close()
    seller = [x for x in result["browserAttempts"] if x["label"] == "seller"]
    products = [x for x in result["browserAttempts"] if x["label"].startswith("product")]
    result["summary"] = {
        "sellerAccessible": any(not x["blockedTerms"] and x["htmlLength"] > 2000 and len(x["productIds"]) >= 10 for x in seller),
        "sellerProductIds": max((len(x["productIds"]) for x in seller), default=0),
        "sellerCategoryIds": sorted({c for x in seller for c in x["categoryIds"]}, key=int),
        "productAccessible": any(not x["blockedTerms"] and x["htmlLength"] > 2000 for x in products),
        "publisherEvidence": sorted({v for x in products for v in x["publisherEvidence"]}),
        "knownUnicornConfirmed": any(re.sub(r"\s+", "", v) == "유니콘" for x in products for v in x["publisherEvidence"]),
    }
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
