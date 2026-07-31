#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import random
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

BASE = "https://shop.coupang.com"
SELLER_ID = "A00214628"
STORE_ID = 79545
FALLBACK = {
    "productId": "1061500342",
    "itemId": "2005918851",
    "vendorItemId": "70005831558",
    "sourceType": "brandstore-all_products",
    "lptag": SELLER_ID,
    "spec": "10799999",
    "src": "1139998",
}
TOKENS = ("유니콘", "저자", "출판사", "publisher", "author", "kc", "cert", "essential", "disclosure", "detail")


def save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_json(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        return None


def hits(value: Any, path: str = "$", out: list[dict[str, str]] | None = None) -> list[dict[str, str]]:
    if out is None:
        out = []
    if len(out) >= 300:
        return out
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            if any(token.lower() in key_text.lower() for token in TOKENS):
                out.append({"path": f"{path}.{key_text}", "value": str(child)[:1600]})
            hits(child, f"{path}.{key_text}", out)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            hits(child, f"{path}[{index}]", out)
    else:
        text = str(value or "")
        if any(token.lower() in text.lower() for token in TOKENS):
            out.append({"path": path, "value": text[:1600]})
    return out


async def browser_post(page, route: str, body: dict[str, Any]) -> dict[str, Any]:
    data = await page.evaluate(
        """async ({route, body}) => {
            const response = await fetch(route, {
                method: 'POST',
                credentials: 'include',
                headers: {
                    'content-type': 'application/json',
                    'accept': 'application/json, text/plain, */*'
                },
                body: JSON.stringify(body),
            });
            const text = await response.text();
            return {
                status: response.status,
                contentType: response.headers.get('content-type') || '',
                length: text.length,
                text: text.slice(0, 500000),
            };
        }""",
        {"route": route, "body": body},
    )
    text = data.pop("text", "")
    row: dict[str, Any] = {**data, "prefix": text[:16000]}
    parsed = parse_json(text)
    if parsed is not None:
        row["topLevelKeys"] = sorted(parsed.keys()) if isinstance(parsed, dict) else []
        row["hits"] = hits(parsed)
        if len(text) <= 500000:
            row["json"] = parsed
    return row


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    result: dict[str, Any] = {
        "fallback": FALLBACK,
        "navigations": [],
        "listingAttempts": [],
        "attempts": [],
    }

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        context = await browser.new_context(
            locale="ko-KR",
            timezone_id="Asia/Seoul",
            viewport={"width": 1440, "height": 1100},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()

        navigation_urls = [
            f"{BASE}/{SELLER_ID}",
            f"{BASE}/{SELLER_ID}?cb={random.randint(100000, 999999)}",
            BASE,
        ]
        for url in navigation_urls:
            try:
                response = await page.goto(url, wait_until="domcontentloaded", timeout=90000)
                nav = {
                    "requested": url,
                    "status": response.status if response else None,
                    "url": page.url,
                    "title": await page.title(),
                }
            except Exception as exc:
                nav = {"requested": url, "error": f"{type(exc).__name__}: {exc}", "url": page.url}
            result["navigations"].append(nav)
            if nav.get("status") == 200:
                break
            await page.wait_for_timeout(2500)

        listing_bodies = [
            {
                "storeId": STORE_ID,
                "brandId": 0,
                "vendorId": SELLER_ID,
                "source": "direct",
                "enableAdultItemDisplay": True,
                "filter": "SORT_KEY:POPULARITY",
            },
            {
                "storeId": STORE_ID,
                "brandId": 0,
                "vendorId": SELLER_ID,
                "source": "direct",
                "enableAdultItemDisplay": True,
                "nextPageKey": 1,
                "filter": "SORT_KEY:POPULARITY",
            },
        ]
        for body in listing_bodies:
            row = {"route": "/api/v1/listing", "body": body}
            try:
                row.update(await browser_post(page, "/api/v1/listing", body))
            except Exception as exc:
                row["error"] = f"{type(exc).__name__}: {exc}"
            result["listingAttempts"].append(row)

        common = {
            "vendorItemIds": [FALLBACK["vendorItemId"]],
            "isVIBased": True,
            "storeId": STORE_ID,
            "vendorId": SELLER_ID,
            "ignoreAdultCheck": True,
        }
        metadata = {
            "lptag": FALLBACK["lptag"],
            "spec": FALLBACK["spec"],
            "src": FALLBACK["src"],
        }
        variants = [
            ("minimal", common),
            ("meta", {**common, **metadata}),
            ("source", {**common, **metadata, "source": FALLBACK["sourceType"]}),
            ("source-store", {**common, **metadata, "source": FALLBACK["sourceType"], "pageType": "STORE"}),
            ("source-brandstore", {**common, **metadata, "source": FALLBACK["sourceType"], "pageType": "BRANDSTORE"}),
            ("source-brandstore-sdp", {**common, **metadata, "source": FALLBACK["sourceType"], "pageType": "BRANDSTORE_SDP"}),
            ("direct-store", {**common, **metadata, "source": "direct", "pageType": "STORE"}),
            ("full-null-free", {
                **common,
                **metadata,
                "source": FALLBACK["sourceType"],
                "pageType": "STORE",
                "sourceFeedId": "",
                "sourceSearchId": "",
                "clickEventId": "",
                "wPcid": "",
            }),
        ]
        variants = [
            (name, {key: value for key, value in body.items() if value not in (None, "")})
            for name, body in variants
        ]

        for route in (
            "/api/v2/store/individualInfo/product",
            "/api/v2/store/individualInfo/products",
        ):
            for name, body in variants:
                row = {"route": route, "variant": name, "body": body}
                try:
                    row.update(await browser_post(page, route, body))
                except Exception as exc:
                    row["error"] = f"{type(exc).__name__}: {exc}"
                result["attempts"].append(row)

        await browser.close()

    result["summary"] = {
        "navigation200": sum(1 for row in result["navigations"] if row.get("status") == 200),
        "listingHttp200": sum(1 for row in result["listingAttempts"] if row.get("status") == 200),
        "attempts": len(result["attempts"]),
        "http200": sum(1 for row in result["attempts"] if row.get("status") == 200),
        "nonEmpty": sum(1 for row in result["attempts"] if (row.get("length") or 0) > 2),
        "rowsWithHits": sum(1 for row in result["attempts"] if row.get("hits")),
    }
    save(args.output, result)
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
