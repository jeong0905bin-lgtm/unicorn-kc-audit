#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import urlencode

from playwright.sync_api import sync_playwright

PRODUCT_ID = "8411161016"
ITEM_ID = "27355912643"
VENDOR_ITEM_ID = "91335726263"
OUT = Path("diagnostics/detail")
BLOCK_TERMS = ("access denied", "captcha", "보안 확인", "비정상적인 접근", "접근 불가", "로그인이 필요")
KEY_TERMS = ("저자", "출판사", "유니콘", "필수 표기", "essential", "product", "vendorItemId", "itemId")


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def clear_all(context, page) -> list[str]:
    notes: list[str] = []
    try:
        context.clear_cookies()
        notes.append("context.clear_cookies=ok")
    except Exception as exc:
        notes.append(f"context.clear_cookies={type(exc).__name__}:{exc}")
    try:
        cdp = context.new_cdp_session(page)
        cdp.send("Network.enable")
        cdp.send("Network.clearBrowserCache")
        cdp.send("Network.clearBrowserCookies")
        for origin in ("https://www.coupang.com", "https://m.coupang.com", "https://shop.coupang.com"):
            try:
                cdp.send("Storage.clearDataForOrigin", {"origin": origin, "storageTypes": "all"})
            except Exception as exc:
                notes.append(f"storage:{origin}={type(exc).__name__}:{exc}")
        cdp.detach()
        notes.append("cdp_cache_cookies_storage=ok")
    except Exception as exc:
        notes.append(f"cdp_clear={type(exc).__name__}:{exc}")
    return notes


def variants() -> list[tuple[str, str, bool]]:
    query = urlencode({"itemId": ITEM_ID, "vendorItemId": VENDOR_ITEM_ID, "sourceType": "brandstore_sdp_atf-all_products"})
    return [
        ("desktop-full", f"https://www.coupang.com/vp/products/{PRODUCT_ID}?{query}", False),
        ("desktop-product-only", f"https://www.coupang.com/vp/products/{PRODUCT_ID}", False),
        ("mobile-full", f"https://m.coupang.com/vm/products/{PRODUCT_ID}?{query}", True),
        ("desktop-user-url", "https://www.coupang.com/vp/products/8411161016?itemId=27355912643&vendorItemId=91335726263", False),
    ]


def run_attempt(browser, label: str, url: str, mobile: bool, attempt: int) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    kwargs = {
        "locale": "ko-KR",
        "timezone_id": "Asia/Seoul",
        "viewport": {"width": 430, "height": 932} if mobile else {"width": 1440, "height": 1200},
        "is_mobile": mobile,
        "has_touch": mobile,
        "service_workers": "block",
        "ignore_https_errors": True,
        "extra_http_headers": {
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.7",
            "Cache-Control": "no-cache, no-store, max-age=0",
            "Pragma": "no-cache",
            "DNT": "1",
        },
    }
    if mobile:
        kwargs["user_agent"] = "Mozilla/5.0 (Linux; Android 15; SM-S928N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36"
    context = browser.new_context(**kwargs)
    context.set_default_timeout(20000)
    page = context.new_page()
    page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
    clear_notes = clear_all(context, page)
    responses: list[dict] = []
    request_failures: list[dict] = []
    console: list[str] = []
    matched_bodies: list[dict] = []

    def on_console(msg):
        try:
            console.append(f"{msg.type}: {msg.text}"[:1000])
        except Exception:
            pass

    def on_request_failed(request):
        try:
            request_failures.append({"url": request.url, "failure": request.failure})
        except Exception:
            pass

    def on_response(response):
        try:
            ctype = (response.headers.get("content-type") or "").lower()
            record = {"status": response.status, "url": response.url, "contentType": ctype[:200]}
            responses.append(record)
            if len(matched_bodies) >= 80 or not any(x in ctype for x in ("json", "html", "text", "javascript")):
                return
            body = response.text()
            low = body.lower()
            if any(term.lower() in low for term in KEY_TERMS):
                snippets = []
                for term in KEY_TERMS:
                    pos = low.find(term.lower())
                    if pos >= 0:
                        snippets.append(clean_text(body[max(0, pos - 300):pos + 1000]))
                matched_bodies.append({"status": response.status, "url": response.url, "snippets": snippets[:8]})
        except Exception:
            pass

    page.on("console", on_console)
    page.on("requestfailed", on_request_failed)
    page.on("response", on_response)
    result = {
        "label": label,
        "attempt": attempt,
        "requestedUrl": url,
        "mobile": mobile,
        "clearNotes": clear_notes,
        "gotoError": "",
        "finalUrl": "",
        "title": "",
        "bodyLength": 0,
        "htmlLength": 0,
        "blockedTerms": [],
        "publisherEvidence": [],
        "responseStatusCounts": {},
        "responses": [],
        "requestFailures": [],
        "console": [],
        "matchedBodies": [],
    }
    try:
        target = url + ("&" if "?" in url else "?") + f"_fresh={int(time.time() * 1000)}-{attempt}"
        try:
            page.goto(target, wait_until="domcontentloaded", timeout=60000)
        except Exception as exc:
            result["gotoError"] = f"{type(exc).__name__}: {exc}"[:2000]
        page.wait_for_timeout(2500)
        for _ in range(5):
            try:
                page.evaluate("window.scrollBy(0, Math.max(window.innerHeight, 1100)); window.dispatchEvent(new Event('scroll')); window.dispatchEvent(new Event('resize'));")
                page.wait_for_timeout(700)
            except Exception:
                break
        try:
            body = page.locator("body").inner_text(timeout=8000)
        except Exception as exc:
            body = f"BODY_ERROR {type(exc).__name__}: {exc}"
        try:
            content = page.content()
        except Exception as exc:
            content = f"HTML_ERROR {type(exc).__name__}: {exc}"
        result["finalUrl"] = page.url
        result["title"] = page.title()[:500]
        result["bodyLength"] = len(body)
        result["htmlLength"] = len(content)
        low = (body + "\n" + content).lower()
        result["blockedTerms"] = [term for term in BLOCK_TERMS if term in low]
        evidence = []
        for term in ("저자", "출판사", "유니콘", "필수 표기"):
            pos = low.find(term.lower())
            if pos >= 0:
                evidence.append(clean_text((body + "\n" + content)[max(0, pos - 500):pos + 1800]))
        result["publisherEvidence"] = evidence[:20]
        stem = OUT / f"{attempt:02d}-{label}"
        stem.with_suffix(".html").write_text(content, encoding="utf-8", errors="ignore")
        stem.with_suffix(".txt").write_text(body, encoding="utf-8", errors="ignore")
        page.screenshot(path=str(stem.with_suffix(".png")), full_page=False)
    except Exception as exc:
        result["fatalError"] = f"{type(exc).__name__}: {exc}"[:2000]
    finally:
        counts: dict[str, int] = {}
        for row in responses:
            key = str(row["status"])
            counts[key] = counts.get(key, 0) + 1
        result["responseStatusCounts"] = counts
        result["responses"] = responses[-400:]
        result["requestFailures"] = request_failures[-200:]
        result["console"] = console[-200:]
        result["matchedBodies"] = matched_bodies[-80:]
        context.close()
    return result


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    all_results = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=[
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-http2",
                "--disable-blink-features=AutomationControlled",
                "--disable-application-cache",
            ],
        )
        try:
            for attempt, (label, url, mobile) in enumerate(variants(), 1):
                result = run_attempt(browser, label, url, mobile, attempt)
                all_results.append(result)
                print(json.dumps({
                    "label": label,
                    "finalUrl": result.get("finalUrl"),
                    "gotoError": result.get("gotoError"),
                    "bodyLength": result.get("bodyLength"),
                    "htmlLength": result.get("htmlLength"),
                    "blockedTerms": result.get("blockedTerms"),
                    "publisherEvidence": len(result.get("publisherEvidence") or []),
                    "statusCounts": result.get("responseStatusCounts"),
                    "requestFailures": len(result.get("requestFailures") or []),
                }, ensure_ascii=False), flush=True)
        finally:
            browser.close()
    summary = {
        "productId": PRODUCT_ID,
        "itemId": ITEM_ID,
        "vendorItemId": VENDOR_ITEM_ID,
        "attempts": all_results,
        "accessibleAttempts": sum(bool(x.get("bodyLength", 0) > 1000 and not x.get("blockedTerms")) for x in all_results),
        "publisherEvidenceAttempts": sum(bool(x.get("publisherEvidence")) for x in all_results),
    }
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"accessibleAttempts": summary["accessibleAttempts"], "publisherEvidenceAttempts": summary["publisherEvidenceAttempts"]}, ensure_ascii=False), flush=True)
    if summary["publisherEvidenceAttempts"] == 0:
        raise SystemExit("No publisher evidence captured; inspect diagnostic artifact")


if __name__ == "__main__":
    main()
