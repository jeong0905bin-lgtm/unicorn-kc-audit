#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from playwright.async_api import async_playwright

BASE = "https://shop.coupang.com"
SELLER_ID = "A00214628"
STORE_ID = 79545
STORE_URL = f"{BASE}/{SELLER_ID}?platform=p"
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
                out.append({"path": f"{path}.{key_text}", "value": str(child)[:1200]})
            hits(child, f"{path}.{key_text}", out)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            hits(child, f"{path}[{index}]", out)
    else:
        text = str(value or "")
        if any(token.lower() in text.lower() for token in TOKENS):
            out.append({"path": path, "value": text[:1200]})
    return out


def first(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key) or []
    return values[0] if values else None


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    result: dict[str, Any] = {"storeUrl": STORE_URL, "navigation": {}, "productLink": None, "attempts": []}

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
        try:
            response = await page.goto(STORE_URL, wait_until="domcontentloaded", timeout=90000)
            result["navigation"] = {
                "status": response.status if response else None,
                "url": page.url,
                "title": await page.title(),
            }
        except Exception as exc:
            result["navigation"] = {"error": f"{type(exc).__name__}: {exc}", "url": page.url}

        for _ in range(12):
            await page.mouse.wheel(0, 1800)
            await page.wait_for_timeout(1000)
        await page.wait_for_timeout(4000)

        links = await page.eval_on_selector_all(
            "a[href*='/vp/products/']",
            "els => Array.from(new Set(els.map(a => a.href).filter(Boolean)))",
        )
        links = sorted(links)
        if not links:
            result["summary"] = {"links": 0, "attempts": 0, "http200": 0, "nonEmpty": 0}
            await browser.close()
            save(args.output, result)
            print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
            return

        link = links[0]
        result["productLink"] = link
        parsed = urlparse(link)
        query = parse_qs(parsed.query)
        match = re.search(r"/vp/products/(\d+)", parsed.path)
        product_id = match.group(1) if match else None
        item_id = first(query, "itemId")
        vendor_item_id = first(query, "vendorItemId")
        source_type = first(query, "sourceType") or "brandstore-all_products"

        common = {
            "vendorItemIds": [vendor_item_id],
            "isVIBased": True,
            "storeId": STORE_ID,
            "vendorId": SELLER_ID,
            "ignoreAdultCheck": True,
        }
        metadata = {
            "sourceSearchId": first(query, "searchId"),
            "lptag": first(query, "lptag"),
            "spec": first(query, "spec"),
            "src": first(query, "src"),
            "wPcid": first(query, "wPcid"),
        }
        metadata = {key: value for key, value in metadata.items() if value not in (None, "")}
        variants = [
            ("minimal", common),
            ("link-meta", {**common, **metadata}),
            ("source-store", {**common, **metadata, "source": source_type, "pageType": "STORE"}),
            ("source-brandstore", {**common, **metadata, "source": source_type, "pageType": "BRANDSTORE"}),
            ("source-brandstore-sdp", {**common, **metadata, "source": source_type, "pageType": "BRANDSTORE_SDP"}),
            ("direct-store", {**common, **metadata, "source": "direct", "pageType": "STORE"}),
            ("source-only", {**common, "source": source_type}),
            ("source-search-only", {**common, "sourceSearchId": first(query, "searchId"), "source": source_type}),
        ]
        variants = [
            (name, {key: value for key, value in body.items() if value not in (None, "")})
            for name, body in variants
        ]

        result["selected"] = {
            "productId": product_id,
            "itemId": item_id,
            "vendorItemId": vendor_item_id,
            "sourceType": source_type,
            "metadata": metadata,
        }

        for route in (
            "/api/v2/store/individualInfo/product",
            "/api/v2/store/individualInfo/products",
        ):
            for name, body in variants:
                row: dict[str, Any] = {"route": route, "variant": name, "body": body}
                try:
                    response_data = await page.evaluate(
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
                                text: text.slice(0, 300000),
                            };
                        }""",
                        {"route": route, "body": body},
                    )
                    row.update({
                        "status": response_data.get("status"),
                        "contentType": response_data.get("contentType"),
                        "length": response_data.get("length"),
                    })
                    text = response_data.get("text", "")
                    row["prefix"] = text[:12000]
                    parsed_json = parse_json(text)
                    if parsed_json is not None:
                        row["topLevelKeys"] = sorted(parsed_json.keys()) if isinstance(parsed_json, dict) else []
                        row["hits"] = hits(parsed_json)
                        row["json"] = parsed_json
                except Exception as exc:
                    row["error"] = f"{type(exc).__name__}: {exc}"
                result["attempts"].append(row)

        await browser.close()

    result["summary"] = {
        "links": len(links),
        "attempts": len(result["attempts"]),
        "http200": sum(1 for row in result["attempts"] if row.get("status") == 200),
        "nonEmpty": sum(1 for row in result["attempts"] if (row.get("length") or 0) > 2),
        "rowsWithHits": sum(1 for row in result["attempts"] if row.get("hits")),
    }
    save(args.output, result)
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
