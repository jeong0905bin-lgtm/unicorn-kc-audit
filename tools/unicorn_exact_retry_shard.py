#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import time
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

BASE = "https://shop.coupang.com"
SELLER_ID = "A00214628"
STORE_ID = 79545
ENDPOINT = "/api/v2/store/individualInfo/product"


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def normalize_label(value: Any) -> str:
    return re.sub(r"[\s,，·ㆍ:：/\\()\[\]_-]+", "", str(value or ""))


def normalize_value(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def classify(row: dict[str, Any], original: dict[str, Any]) -> dict[str, Any]:
    attributes = row.get("attributes") if isinstance(row.get("attributes"), list) else []
    publisher_fields = [
        {
            "id": item.get("id"),
            "name": str(item.get("name") or ""),
            "value": str(item.get("value") or ""),
            "expose": item.get("expose"),
        }
        for item in attributes
        if isinstance(item, dict) and "출판사" in normalize_label(item.get("name"))
    ]
    exact = [item for item in publisher_fields if normalize_value(item.get("value")) == "유니콘"]
    row.update({
        "catalogIndex": int(original.get("catalogIndex")),
        "catalogProductId": str(original.get("catalogProductId") or original.get("productId") or ""),
        "catalogItemId": str(original.get("catalogItemId") or original.get("itemId") or ""),
        "catalogVendorItemId": str(original.get("catalogVendorItemId") or original.get("requestedVendorItemId") or original.get("vendorItemId") or ""),
        "catalogSourceName": str(original.get("catalogSourceName") or original.get("sourceName") or ""),
        "catalogProductUrl": str(original.get("catalogProductUrl") or original.get("productUrl") or ""),
        "publisherFields": publisher_fields,
        "publisherExactMatches": exact,
        "publisherExactUnicorn": bool(exact),
    })
    return row


def success(row: dict[str, Any]) -> bool:
    return row.get("httpStatus") == 200 and int(row.get("apiCode") or 0) == 200 and bool(row.get("dataPresent"))


async def open_page(context, round_number: int, sequence: int):
    page = await context.new_page()
    page.set_default_timeout(60_000)
    url = f"{BASE}/{SELLER_ID}?retryRound={round_number}&sequence={sequence}&cb={random.randint(100000, 999999)}"
    navigation: dict[str, Any]
    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        navigation = {
            "requested": url,
            "status": response.status if response else None,
            "url": page.url,
            "title": await page.title(),
        }
    except Exception as exc:
        navigation = {"requested": url, "error": f"{type(exc).__name__}: {exc}", "url": page.url}
    return page, navigation


async def fetch_batch(page, items: list[dict[str, Any]], concurrency: int) -> list[dict[str, Any]]:
    return await page.evaluate(
        """async ({items, endpoint, storeId, vendorId, concurrency}) => {
            const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
            const compactSignals = data => {
                const output = [];
                const seen = new Set();
                const walk = (value, path, depth) => {
                    if (value == null || depth > 8 || output.length >= 120) return;
                    if (Array.isArray(value)) {
                        value.slice(0, 100).forEach((child, index) => walk(child, `${path}[${index}]`, depth + 1));
                        return;
                    }
                    if (typeof value === 'object') {
                        for (const [key, child] of Object.entries(value)) {
                            const nextPath = `${path}.${key}`;
                            if (/kc|cert|safety|auth|인증|안전/i.test(key)) {
                                let text;
                                try { text = typeof child === 'string' ? child : JSON.stringify(child); }
                                catch { text = String(child); }
                                const token = `${nextPath}:${text}`;
                                if (!seen.has(token)) {
                                    seen.add(token);
                                    output.push({path: nextPath, value: text.slice(0, 1800)});
                                }
                            }
                            walk(child, nextPath, depth + 1);
                        }
                        return;
                    }
                    const text = String(value);
                    if (/\b[A-Z]{1,4}\d{2,}[A-Z]?\d*(?:-\d+)+\b/i.test(text) || /KC\s*(?:인증|번호)/i.test(text)) {
                        const token = `${path}:${text}`;
                        if (!seen.has(token)) {
                            seen.add(token);
                            output.push({path, value: text.slice(0, 1800)});
                        }
                    }
                };
                walk(data, '$', 0);
                return output;
            };
            const requestOne = async item => {
                const controller = new AbortController();
                const timer = setTimeout(() => controller.abort(), 18000);
                try {
                    const response = await fetch(endpoint, {
                        method: 'POST',
                        credentials: 'include',
                        signal: controller.signal,
                        headers: {
                            'content-type': 'application/json',
                            'accept': 'application/json, text/plain, */*'
                        },
                        body: JSON.stringify({
                            vendorItemIds: [item.vendorItemId],
                            isVIBased: true,
                            storeId,
                            vendorId,
                            ignoreAdultCheck: true
                        })
                    });
                    const text = await response.text();
                    let parsed = null;
                    try { parsed = JSON.parse(text); } catch {}
                    const data = parsed && parsed.data && typeof parsed.data === 'object' ? parsed.data : null;
                    return {
                        catalogIndex: item.catalogIndex,
                        requestedVendorItemId: item.vendorItemId,
                        httpStatus: response.status,
                        responseLength: text.length,
                        responsePrefix: text.slice(0, 700),
                        apiCode: parsed ? parsed.code : null,
                        apiMessage: parsed ? (parsed.msg || parsed.message || null) : null,
                        dataPresent: Boolean(data),
                        productId: data ? String(data.productId || '') : '',
                        itemId: data ? String(data.itemId || '') : '',
                        vendorItemId: data ? String(data.vendorItemId || item.vendorItemId || '') : String(item.vendorItemId || ''),
                        sourceName: data && data.imageAndTitleArea ? String(data.imageAndTitleArea.title || '') : '',
                        groupTitle: data && data.imageAndTitleArea ? String(data.imageAndTitleArea.groupTitle || '') : '',
                        mainImageUrl: data && data.imageAndTitleArea ? String(data.imageAndTitleArea.completeHttpUrl || data.imageAndTitleArea.defaultUrl || '') : '',
                        detailImageUrls: data && data.imageAndTitleArea ? (data.imageAndTitleArea.completeHttpDetailImageUrls || data.imageAndTitleArea.detailImageUrls || []) : [],
                        attributes: data && Array.isArray(data.attributes) ? data.attributes : [],
                        valid: data ? data.valid : null,
                        adult: data ? data.adult : null,
                        productLink: data ? String(data.link || '') : '',
                        kcSignals: data ? compactSignals(data) : []
                    };
                } catch (error) {
                    return {
                        catalogIndex: item.catalogIndex,
                        requestedVendorItemId: item.vendorItemId,
                        httpStatus: null,
                        dataPresent: false,
                        error: `${error && error.name ? error.name : 'Error'}: ${error && error.message ? error.message : String(error)}`
                    };
                } finally {
                    clearTimeout(timer);
                }
            };
            const results = new Array(items.length);
            let cursor = 0;
            const worker = async () => {
                while (true) {
                    const index = cursor++;
                    if (index >= items.length) return;
                    results[index] = await requestOne(items[index]);
                    await sleep(160 + Math.floor(Math.random() * 240));
                }
            };
            await Promise.all(Array.from({length: Math.max(1, concurrency)}, worker));
            return results;
        }""",
        {
            "items": items,
            "endpoint": ENDPOINT,
            "storeId": STORE_ID,
            "vendorId": SELLER_ID,
            "concurrency": max(1, concurrency),
        },
    )


async def main_async() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--unresolved", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--shard-index", type=int, required=True)
    parser.add_argument("--shard-count", type=int, required=True)
    parser.add_argument("--rounds", type=int, default=4)
    args = parser.parse_args()

    document = json.loads(args.unresolved.read_text(encoding="utf-8"))
    all_products = list(document.get("products") or [])
    products = [
        product for product in all_products
        if int(product.get("catalogIndex")) % args.shard_count == args.shard_index
    ]
    originals = {int(product["catalogIndex"]): product for product in products}
    pending = sorted(originals)
    recovered: dict[int, dict[str, Any]] = {}
    latest: dict[int, dict[str, Any]] = {}
    attempts: list[dict[str, Any]] = []
    navigations: list[dict[str, Any]] = []
    started = time.time()

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            locale="ko-KR",
            timezone_id="Asia/Seoul",
            viewport={"width": 1440, "height": 1000},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
            ),
        )

        sequence = 0
        for round_number in range(1, args.rounds + 1):
            if not pending:
                break
            round_indices = list(pending)
            pending = []
            for start in range(0, len(round_indices), 16):
                sequence += 1
                page, navigation = await open_page(context, round_number, sequence)
                navigation.update({"round": round_number, "sequence": sequence})
                navigations.append(navigation)
                indices = round_indices[start:start + 16]
                batch = [
                    {
                        "catalogIndex": index,
                        "vendorItemId": str(
                            originals[index].get("catalogVendorItemId")
                            or originals[index].get("requestedVendorItemId")
                            or originals[index].get("vendorItemId")
                            or ""
                        ),
                    }
                    for index in indices
                ]
                try:
                    rows = await fetch_batch(page, batch, concurrency=2 if round_number <= 2 else 1)
                except Exception as exc:
                    rows = [
                        {
                            "catalogIndex": item["catalogIndex"],
                            "requestedVendorItemId": item["vendorItemId"],
                            "httpStatus": None,
                            "dataPresent": False,
                            "error": f"batch failure: {type(exc).__name__}: {exc}",
                        }
                        for item in batch
                    ]
                await page.close()

                for row in rows:
                    index = int(row["catalogIndex"])
                    row["retryRound"] = round_number
                    row["shardIndex"] = args.shard_index
                    row = classify(row, originals[index])
                    latest[index] = row
                    attempts.append({
                        "catalogIndex": index,
                        "retryRound": round_number,
                        "httpStatus": row.get("httpStatus"),
                        "apiCode": row.get("apiCode"),
                        "responseLength": row.get("responseLength"),
                        "dataPresent": row.get("dataPresent"),
                        "error": row.get("error"),
                    })
                    if success(row):
                        recovered[index] = row
                    else:
                        pending.append(index)
                await asyncio.sleep(1.5 + random.random() * 1.5)

            pending = sorted(set(pending) - set(recovered))
            print(json.dumps({
                "shard": args.shard_index,
                "round": round_number,
                "input": len(round_indices),
                "recoveredTotal": len(recovered),
                "remaining": len(pending),
                "elapsedSeconds": round(time.time() - started, 2),
            }, ensure_ascii=False), flush=True)
            if pending:
                await asyncio.sleep(5 + round_number * 3 + random.random() * 4)

        await browser.close()

    unresolved_rows = [latest.get(index) or originals[index] for index in pending]
    recovered_rows = [recovered[index] for index in sorted(recovered)]
    exact_rows = [row for row in recovered_rows if row.get("publisherExactUnicorn")]
    result = {
        "sourceUnresolvedCount": len(all_products),
        "shardIndex": args.shard_index,
        "shardCount": args.shard_count,
        "inputCount": len(products),
        "recoveredCount": len(recovered_rows),
        "unresolvedCount": len(unresolved_rows),
        "newExactUnicornCount": len(exact_rows),
        "elapsedSeconds": round(time.time() - started, 2),
        "recovered": recovered_rows,
        "exactUnicorn": exact_rows,
        "unresolved": unresolved_rows,
        "attempts": attempts,
        "navigations": navigations,
    }
    save_json(args.output, result)
    print(json.dumps({key: result[key] for key in (
        "shardIndex", "inputCount", "recoveredCount", "unresolvedCount", "newExactUnicornCount", "elapsedSeconds"
    )}, ensure_ascii=False, indent=2))


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
