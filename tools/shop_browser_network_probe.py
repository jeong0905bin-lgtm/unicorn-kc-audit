#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

BASE = "https://shop.coupang.com"
SELLER_ID = "A00214628"
STORE_URL = f"{BASE}/{SELLER_ID}"
KNOWN = {
    "productId": "8411161016",
    "itemId": "24319968314",
    "vendorItemId": "91335726263",
}
SAFE_REQUEST_HEADERS = {
    "accept",
    "accept-language",
    "content-type",
    "origin",
    "referer",
    "user-agent",
    "x-requested-with",
}
TOKENS = (
    "유니콘",
    "저자",
    "출판사",
    "publisher",
    "author",
    "kc",
    "cert",
    "essential",
    "disclosure",
    "detail",
    KNOWN["productId"],
    KNOWN["itemId"],
    KNOWN["vendorItemId"],
)


def save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def safe_headers(headers: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() in SAFE_REQUEST_HEADERS}


def parse_json(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        return None


def hits(value: Any, path: str = "$", out: list[dict[str, str]] | None = None) -> list[dict[str, str]]:
    if out is None:
        out = []
    if len(out) >= 500:
        return out
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            if any(token.lower() in key_text.lower() for token in TOKENS):
                out.append({"path": f"{path}.{key_text}", "value": str(child)[:1500]})
            hits(child, f"{path}.{key_text}", out)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            hits(child, f"{path}[{index}]", out)
    else:
        text = str(value or "")
        if any(token.lower() in text.lower() for token in TOKENS):
            out.append({"path": path, "value": text[:1500]})
    return out


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    result: dict[str, Any] = {
        "storeUrl": STORE_URL,
        "known": KNOWN,
        "navigation": {},
        "apiTraffic": [],
        "productLinks": [],
        "replays": [],
        "pageSignals": {},
    }
    request_rows: dict[int, dict[str, Any]] = {}
    response_tasks: list[asyncio.Task[Any]] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        context = await browser.new_context(
            locale="ko-KR",
            timezone_id="Asia/Seoul",
            viewport={"width": 1440, "height": 1100},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/150.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()

        def on_request(request) -> None:
            if "/api/" not in request.url or "shop.coupang.com" not in request.url:
                return
            raw = request.post_data or ""
            row: dict[str, Any] = {
                "method": request.method,
                "url": request.url,
                "resourceType": request.resource_type,
                "headers": safe_headers(request.headers),
                "postData": raw[:20000],
            }
            parsed = parse_json(raw)
            if parsed is not None:
                row["postJson"] = parsed
            request_rows[id(request)] = row
            result["apiTraffic"].append(row)

        async def on_response(response) -> None:
            request = response.request
            row = request_rows.get(id(request))
            if row is None:
                return
            row["status"] = response.status
            row["responseHeaders"] = safe_headers(await response.all_headers())
            try:
                body = await response.body()
            except Exception as exc:
                row["responseError"] = f"{type(exc).__name__}: {exc}"
                return
            row["responseLength"] = len(body)
            text = body.decode("utf-8", errors="replace")
            row["responsePrefix"] = text[:8000]
            parsed = parse_json(text)
            if parsed is not None:
                row["responseTopLevelKeys"] = sorted(parsed.keys()) if isinstance(parsed, dict) else []
                row["responseHits"] = hits(parsed)
                if len(body) <= 350000 and any(
                    route in request.url
                    for route in (
                        "/api/v2/store/individualInfo/product",
                        "/api/v2/store/individualInfo/products",
                    )
                ):
                    row["responseJson"] = parsed

        page.on("request", on_request)
        page.on("response", lambda response: response_tasks.append(asyncio.create_task(on_response(response))))

        try:
            response = await page.goto(STORE_URL, wait_until="domcontentloaded", timeout=90000)
            result["navigation"] = {
                "status": response.status if response else None,
                "url": page.url,
                "title": await page.title(),
            }
        except Exception as exc:
            result["navigation"] = {"error": f"{type(exc).__name__}: {exc}", "url": page.url}

        for _ in range(10):
            await page.mouse.wheel(0, 1800)
            await page.wait_for_timeout(1200)
        await page.wait_for_timeout(5000)

        try:
            links = await page.eval_on_selector_all(
                "a[href]",
                "els => els.map(a => a.href).filter(Boolean)",
            )
        except Exception:
            links = []
        product_links = sorted(
            {
                link
                for link in links
                if "/vp/products/" in link or re.search(r"/products/\d+", link)
            }
        )
        result["productLinks"] = product_links[:100]

        try:
            html = await page.content()
        except Exception:
            html = ""
        result["pageSignals"] = {
            "htmlLength": len(html),
            "containsKnownProductId": KNOWN["productId"] in html,
            "containsKnownVendorItemId": KNOWN["vendorItemId"] in html,
            "containsPublisherLabel": "저자, 출판사" in html,
            "containsUnicorn": "유니콘" in html,
            "snippets": [],
        }
        for token in TOKENS:
            for match in list(re.finditer(re.escape(token), html, flags=re.IGNORECASE))[:3]:
                start = max(0, match.start() - 300)
                end = min(len(html), match.end() + 700)
                result["pageSignals"]["snippets"].append({"token": token, "text": html[start:end]})

        individual_rows = [
            row
            for row in result["apiTraffic"]
            if "/api/v2/store/individualInfo/product" in row.get("url", "") and row.get("postJson")
        ]
        for row in individual_rows[:10]:
            route = re.sub(r"^https://shop\.coupang\.com", "", row["url"])
            body = row["postJson"]
            replay = {"route": route, "body": body}
            try:
                replay_result = await page.evaluate(
                    """async ({route, body}) => {
                        const response = await fetch(route, {
                            method: 'POST',
                            credentials: 'include',
                            headers: {'content-type': 'application/json', 'accept': 'application/json, text/plain, */*'},
                            body: JSON.stringify(body),
                        });
                        const text = await response.text();
                        return {status: response.status, length: text.length, prefix: text.slice(0, 8000)};
                    }""",
                    {"route": route, "body": body},
                )
                replay.update(replay_result)
                parsed = parse_json(replay_result.get("prefix", ""))
                if parsed is not None:
                    replay["hits"] = hits(parsed)
            except Exception as exc:
                replay["error"] = f"{type(exc).__name__}: {exc}"
            result["replays"].append(replay)

        if product_links:
            detail = await context.new_page()
            detail.on("request", on_request)
            detail.on("response", lambda response: response_tasks.append(asyncio.create_task(on_response(response))))
            try:
                response = await detail.goto(product_links[0], wait_until="domcontentloaded", timeout=90000)
                result["detailNavigation"] = {
                    "requested": product_links[0],
                    "status": response.status if response else None,
                    "url": detail.url,
                    "title": await detail.title(),
                }
                await detail.wait_for_timeout(8000)
            except Exception as exc:
                result["detailNavigation"] = {
                    "requested": product_links[0],
                    "error": f"{type(exc).__name__}: {exc}",
                    "url": detail.url,
                }
            await detail.close()

        if response_tasks:
            await asyncio.gather(*response_tasks, return_exceptions=True)
        await browser.close()

    result["summary"] = {
        "apiRequests": len(result["apiTraffic"]),
        "individualInfoRequests": sum(
            1 for row in result["apiTraffic"] if "/api/v2/store/individualInfo/product" in row.get("url", "")
        ),
        "successfulIndividualInfoResponses": sum(
            1
            for row in result["apiTraffic"]
            if "/api/v2/store/individualInfo/product" in row.get("url", "") and row.get("status") == 200
        ),
        "individualInfoRowsWithHits": sum(
            1
            for row in result["apiTraffic"]
            if "/api/v2/store/individualInfo/product" in row.get("url", "") and row.get("responseHits")
        ),
        "productLinks": len(result["productLinks"]),
    }
    save(args.output, result)
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
