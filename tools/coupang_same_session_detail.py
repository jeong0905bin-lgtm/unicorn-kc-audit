#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from playwright.sync_api import sync_playwright

SELLER_URL = "https://shop.coupang.com/A00214628?source=brandstore_sdp_atf&ocid=1208642&checkBatchDelivery=true&pid=8411161016&viid=91335726263&platform=p&brandId=0&btcEnableForce=false"
PRODUCT_URL = "https://www.coupang.com/vp/products/8411161016?itemId=27355912643&vendorItemId=91335726263&sourceType=brandstore_sdp_atf&vendorId=A00214628&storeId=79545"
BLOCK_TERMS = ("access denied", "captcha", "보안 확인", "비정상적인 접근", "접근 불가", "로그인이 필요")
KEY_TERMS = ("저자", "출판사", "유니콘", "품명 및 모델명", "KC 인증정보", "필수 표기")


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", value or " ").strip()


def evidence(text: str) -> list[str]:
    low = text.lower()
    out: list[str] = []
    for term in KEY_TERMS:
        pos = low.find(term.lower())
        if pos >= 0:
            out.append(clean(text[max(0, pos - 500):pos + 2200]))
    return out[:30]


def publisher_value(text: str) -> str:
    patterns = (
        r"저자\s*[,·/ㆍ]\s*출판사\s*[:：]?\s*([^\n|<>]{1,120})",
        r"저자\s*출판사\s*[:：]?\s*([^\n|<>]{1,120})",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            value = re.split(r"배송|교환|반품|크기|쪽수|제조", match.group(1))[0]
            return clean(value).strip(" :：|,·ㆍ/-")[:120]
    return ""


def run(browser_name: str, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser_type = getattr(pw, browser_name)
        browser = browser_type.launch(headless=True, args=["--disable-dev-shm-usage", "--no-sandbox"] if browser_name == "chromium" else [])
        context = browser.new_context(
            locale="ko-KR",
            timezone_id="Asia/Seoul",
            viewport={"width": 1440, "height": 1200},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
            extra_http_headers={
                "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.7",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            },
        )
        context.set_default_timeout(20000)
        page = context.new_page()
        network: list[dict] = []
        matched_bodies: list[dict] = []

        def on_response(response):
            try:
                ctype = (response.headers.get("content-type") or "").lower()
                network.append({"status": response.status, "url": response.url, "contentType": ctype[:160]})
                if len(matched_bodies) >= 120 or not any(x in ctype for x in ("json", "html", "text", "javascript")):
                    return
                body = response.text()
                found = evidence(body)
                if found:
                    matched_bodies.append({"status": response.status, "url": response.url, "evidence": found})
            except Exception:
                pass

        page.on("response", on_response)
        attempts: list[dict] = []
        try:
            page.goto(SELLER_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)
            seller_body = page.locator("body").inner_text(timeout=10000)
            seller_html = page.content()
            cookie_names = sorted({cookie.get("name", "") for cookie in context.cookies() if cookie.get("name")})
            (out_dir / "seller.html").write_text(seller_html, encoding="utf-8", errors="ignore")
            (out_dir / "seller.txt").write_text(seller_body, encoding="utf-8", errors="ignore")

            methods = ("same_page_location", "new_page_referer", "context_request")
            for method in methods:
                result = {"method": method, "status": 0, "finalUrl": "", "bodyLength": 0, "htmlLength": 0, "blockedTerms": [], "publisherValue": "", "evidence": [], "error": ""}
                try:
                    if method == "same_page_location":
                        page.goto(SELLER_URL, wait_until="domcontentloaded", timeout=60000)
                        page.wait_for_timeout(1500)
                        page.evaluate("url => { window.location.assign(url); }", PRODUCT_URL)
                        page.wait_for_load_state("domcontentloaded", timeout=60000)
                        page.wait_for_timeout(3500)
                        for _ in range(6):
                            page.evaluate("window.scrollBy(0, Math.max(window.innerHeight, 1200)); window.dispatchEvent(new Event('scroll'))")
                            page.wait_for_timeout(500)
                        body = page.locator("body").inner_text(timeout=10000)
                        html = page.content()
                        result["status"] = 200
                        result["finalUrl"] = page.url
                    elif method == "new_page_referer":
                        p2 = context.new_page()
                        p2.on("response", on_response)
                        response = p2.goto(PRODUCT_URL, wait_until="domcontentloaded", timeout=60000, referer=SELLER_URL)
                        p2.wait_for_timeout(3500)
                        for _ in range(6):
                            p2.evaluate("window.scrollBy(0, Math.max(window.innerHeight, 1200)); window.dispatchEvent(new Event('scroll'))")
                            p2.wait_for_timeout(500)
                        body = p2.locator("body").inner_text(timeout=10000)
                        html = p2.content()
                        result["status"] = response.status if response else 0
                        result["finalUrl"] = p2.url
                        p2.close()
                    else:
                        response = context.request.get(PRODUCT_URL, headers={"Referer": SELLER_URL, "Accept-Language": "ko-KR,ko;q=0.9"}, timeout=60000)
                        body = response.text()
                        html = body
                        result["status"] = response.status
                        result["finalUrl"] = PRODUCT_URL
                    combined = body + "\n" + html + "\n" + "\n".join(x for row in matched_bodies for x in row.get("evidence", []))
                    result["bodyLength"] = len(body)
                    result["htmlLength"] = len(html)
                    low = combined.lower()
                    result["blockedTerms"] = [term for term in BLOCK_TERMS if term in low]
                    result["publisherValue"] = publisher_value(combined)
                    result["evidence"] = evidence(combined)
                    (out_dir / f"{method}.html").write_text(html, encoding="utf-8", errors="ignore")
                    (out_dir / f"{method}.txt").write_text(body, encoding="utf-8", errors="ignore")
                except Exception as exc:
                    result["error"] = f"{type(exc).__name__}: {exc}"[:2000]
                attempts.append(result)
                print(json.dumps(result, ensure_ascii=False), flush=True)

            status_counts = Counter(str(row.get("status")) for row in network)
            summary = {
                "browser": browser_name,
                "sellerUrl": SELLER_URL,
                "productUrl": PRODUCT_URL,
                "sellerBodyLength": len(seller_body),
                "cookieNames": cookie_names,
                "attempts": attempts,
                "publisherConfirmed": any(re.sub(r"\s+", "", str(x.get("publisherValue") or "")) == "유니콘" for x in attempts),
                "publisherEvidenceAttempts": sum(bool(x.get("evidence")) for x in attempts),
                "responseStatusCounts": dict(status_counts),
                "matchedBodies": matched_bodies[-120:],
                "network": network[-1000:],
            }
            (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({"browser": browser_name, "publisherConfirmed": summary["publisherConfirmed"], "publisherEvidenceAttempts": summary["publisherEvidenceAttempts"]}, ensure_ascii=False), flush=True)
            if not summary["publisherConfirmed"]:
                raise SystemExit("same-session navigation did not confirm exact publisher 유니콘")
        finally:
            browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser", choices=["chromium", "firefox", "webkit"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.browser, args.output)
