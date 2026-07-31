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

from playwright.async_api import Page, async_playwright

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
    return re.sub(r"[\s,，·ㆍ:：/\\()\[\]_-]+", "", str(value or "")).strip()


def normalize_value(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def publisher_fields(attributes: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in attributes or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        if "출판사" not in normalize_label(name):
            continue
        rows.append({
            "id": item.get("id"),
            "name": name,
            "value": str(item.get("value") or ""),
            "expose": item.get("expose"),
        })
    return rows


def classify(row: dict[str, Any]) -> dict[str, Any]:
    fields = publisher_fields(row.get("attributes"))
    exact = [field for field in fields if normalize_value(field.get("value")) == "유니콘"]
    row["publisherFields"] = fields
    row["publisherExactUnicorn"] = bool(exact)
    row["publisherExactMatches"] = exact
    return row


async def prepare_page(context, attempts: int = 4) -> tuple[Page, list[dict[str, Any]]]:
    page = await context.new_page()
    page.set_default_timeout(180_000)
    navigations: list[dict[str, Any]] = []
    urls = [
        f"{BASE}/{SELLER_ID}",
        f"{BASE}/{SELLER_ID}?cb={random.randint(100000, 999999)}",
        BASE,
    ]
    for attempt in range(attempts):
        url = urls[attempt % len(urls)]
        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=90_000)
            navigations.append({
                "requested": url,
                "status": response.status if response else None,
                "url": page.url,
                "title": await page.title(),
            })
        except Exception as exc:
            navigations.append({
                "requested": url,
                "error": f"{type(exc).__name__}: {exc}",
                "url": page.url,
            })
        # Endpoint calls can work even when the document navigation is denied.
        if page.url.startswith(BASE):
            break
        await page.wait_for_timeout(1500)
    return page, navigations


async def fetch_chunk(
    page: Page,
    items: list[dict[str, Any]],
    *,
    concurrency: int,
    base_delay_ms: int,
    retries: int,
) -> list[dict[str, Any]]:
    return await page.evaluate(
        """async ({items, endpoint, storeId, vendorId, concurrency, baseDelayMs, retries}) => {
            const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));
            const compactSignals = (value) => {
                const out = [];
                const seen = new Set();
                const visit = (node, path, depth) => {
                    if (out.length >= 120 || depth > 8 || node == null) return;
                    if (Array.isArray(node)) {
                        node.slice(0, 100).forEach((child, index) => visit(child, `${path}[${index}]`, depth + 1));
                        return;
                    }
                    if (typeof node === 'object') {
                        for (const [key, child] of Object.entries(node)) {
                            const next = `${path}.${key}`;
                            if (/kc|cert|safety|auth|인증|안전/i.test(key)) {
                                const text = typeof child === 'string' ? child : JSON.stringify(child);
                                const token = `${next}:${text}`;
                                if (!seen.has(token)) {
                                    seen.add(token);
                                    out.push({path: next, value: text.slice(0, 1500)});
                                }
                            }
                            visit(child, next, depth + 1);
                        }
                        return;
                    }
                    const text = String(node);
                    if (/\b[A-Z]{1,4}\d{2,}[A-Z]?\d*(?:-\d+)+\b/i.test(text) || /KC\s*(?:인증|번호)/i.test(text)) {
                        const token = `${path}:${text}`;
                        if (!seen.has(token)) {
                            seen.add(token);
                            out.push({path, value: text.slice(0, 1500)});
                        }
                    }
                };
                visit(value, '$', 0);
                return out;
            };
            const requestOne = async (item) => {
                let last = null;
                for (let attempt = 1; attempt <= retries; attempt += 1) {
                    try {
                        const response = await fetch(endpoint, {
                            method: 'POST',
                            credentials: 'include',
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
                        try { parsed = JSON.parse(text); } catch (_) {}
                        const data = parsed && parsed.data && typeof parsed.data === 'object' ? parsed.data : null;
                        last = {
                            catalogIndex: item.catalogIndex,
                            requestedVendorItemId: item.vendorItemId,
                            httpStatus: response.status,
                            contentType: response.headers.get('content-type') || '',
                            responseLength: text.length,
                            apiCode: parsed ? parsed.code : null,
                            apiMessage: parsed ? (parsed.msg || parsed.message || null) : null,
                            responsePrefix: text.slice(0, 600),
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
                            kcSignals: data ? compactSignals(data) : [],
                            attempt
                        };
                        if (response.status === 200 && parsed && Number(parsed.code) === 200 && data) return last;
                        if (![403, 408, 425, 429, 500, 502, 503, 504].includes(response.status)) return last;
                    } catch (error) {
                        last = {
                            catalogIndex: item.catalogIndex,
                            requestedVendorItemId: item.vendorItemId,
                            httpStatus: null,
                            error: `${error && error.name ? error.name : 'Error'}: ${error && error.message ? error.message : String(error)}`,
                            attempt
                        };
                    }
                    await sleep(Math.min(12000, baseDelayMs * Math.pow(2, attempt - 1) + Math.floor(Math.random() * 500)));
                }
                return last;
            };
            const results = new Array(items.length);
            let cursor = 0;
            const worker = async () => {
                while (true) {
                    const index = cursor++;
                    if (index >= items.length) return;
                    results[index] = await requestOne(items[index]);
                    await sleep(baseDelayMs + Math.floor(Math.random() * Math.max(50, baseDelayMs)));
                }
            };
            await Promise.all(Array.from({length: Math.max(1, concurrency)}, () => worker()));
            return results;
        }""",
        {
            "items": items,
            "endpoint": ENDPOINT,
            "storeId": STORE_ID,
            "vendorId": SELLER_ID,
            "concurrency": concurrency,
            "baseDelayMs": base_delay_ms,
            "retries": retries,
        },
    )


def build_summary(catalog: list[dict[str, Any]], rows: list[dict[str, Any]]) -> dict[str, Any]:
    successful = [row for row in rows if row.get("httpStatus") == 200 and int(row.get("apiCode") or 0) == 200]
    exact = [row for row in successful if row.get("publisherExactUnicorn")]
    unresolved = [row for row in rows if not (row.get("httpStatus") == 200 and int(row.get("apiCode") or 0) == 200)]
    with_publisher = [row for row in successful if row.get("publisherFields")]
    return {
        "sellerId": SELLER_ID,
        "storeId": STORE_ID,
        "catalogCount": len(catalog),
        "processedCount": len(rows),
        "successfulCount": len(successful),
        "unresolvedCount": len(unresolved),
        "publisherFieldCount": len(with_publisher),
        "publisherExactUnicornCount": len(exact),
        "httpStatusCounts": {
            str(status): sum(1 for row in rows if row.get("httpStatus") == status)
            for status in sorted({row.get("httpStatus") for row in rows}, key=lambda value: (value is None, value or 0))
        },
    }


async def main_async() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--chunk-size", type=int, default=50)
    parser.add_argument("--concurrency", type=int, default=3)
    args = parser.parse_args()

    catalog_doc = json.loads(args.catalog.read_text(encoding="utf-8"))
    catalog = list(catalog_doc.get("products") or [])
    if len(catalog) < 1500:
        raise SystemExit(f"catalog is unexpectedly small: {len(catalog)}")

    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    rows_by_index: dict[int, dict[str, Any]] = {}
    navigation_log: list[dict[str, Any]] = []
    started = time.time()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            locale="ko-KR",
            timezone_id="Asia/Seoul",
            viewport={"width": 1440, "height": 1100},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
            ),
        )
        page, navigations = await prepare_page(context)
        navigation_log.extend(navigations)

        items = [
            {
                "catalogIndex": index,
                "vendorItemId": str(product.get("vendorItemId") or ""),
            }
            for index, product in enumerate(catalog)
            if str(product.get("vendorItemId") or "")
        ]
        missing_vendor = [
            index for index, product in enumerate(catalog)
            if not str(product.get("vendorItemId") or "")
        ]
        for index in missing_vendor:
            rows_by_index[index] = classify({
                "catalogIndex": index,
                "requestedVendorItemId": "",
                "httpStatus": None,
                "error": "catalog row has no vendorItemId",
            })

        for start in range(0, len(items), args.chunk_size):
            chunk = items[start:start + args.chunk_size]
            try:
                chunk_rows = await fetch_chunk(
                    page,
                    chunk,
                    concurrency=max(1, args.concurrency),
                    base_delay_ms=120,
                    retries=4,
                )
            except Exception as exc:
                chunk_rows = [
                    {
                        "catalogIndex": item["catalogIndex"],
                        "requestedVendorItemId": item["vendorItemId"],
                        "httpStatus": None,
                        "error": f"chunk failure: {type(exc).__name__}: {exc}",
                    }
                    for item in chunk
                ]
            for row in chunk_rows:
                index = int(row["catalogIndex"])
                catalog_row = catalog[index]
                row["catalogProductId"] = str(catalog_row.get("productId") or "")
                row["catalogItemId"] = str(catalog_row.get("itemId") or "")
                row["catalogVendorItemId"] = str(catalog_row.get("vendorItemId") or "")
                row["catalogSourceName"] = str(catalog_row.get("sourceName") or "")
                row["catalogProductUrl"] = str(catalog_row.get("productUrl") or "")
                rows_by_index[index] = classify(row)

            ordered = [rows_by_index[index] for index in sorted(rows_by_index)]
            summary = build_summary(catalog, ordered)
            summary["elapsedSeconds"] = round(time.time() - started, 2)
            summary["lastCatalogIndex"] = max(rows_by_index) if rows_by_index else -1
            save_json(output_dir / "checkpoint.json", {
                "summary": summary,
                "navigations": navigation_log,
                "rows": ordered,
            })
            print(json.dumps({
                "processed": summary["processedCount"],
                "successful": summary["successfulCount"],
                "unresolved": summary["unresolvedCount"],
                "exactUnicorn": summary["publisherExactUnicornCount"],
                "elapsedSeconds": summary["elapsedSeconds"],
            }, ensure_ascii=False), flush=True)

            recent = chunk_rows
            blocked = sum(1 for row in recent if row.get("httpStatus") in (403, 429))
            if recent and blocked / len(recent) >= 0.25:
                await page.close()
                await asyncio.sleep(12)
                page, navigations = await prepare_page(context)
                navigation_log.extend(navigations)
            else:
                await asyncio.sleep(0.8)

        # Retry only unresolved rows at a slower rate in two passes.
        for retry_round in range(1, 3):
            unresolved_indices = [
                index for index, row in rows_by_index.items()
                if not (row.get("httpStatus") == 200 and int(row.get("apiCode") or 0) == 200)
                and str(catalog[index].get("vendorItemId") or "")
            ]
            if not unresolved_indices:
                break
            await page.close()
            await asyncio.sleep(8 * retry_round)
            page, navigations = await prepare_page(context)
            navigation_log.extend(navigations)
            for offset in range(0, len(unresolved_indices), 25):
                indices = unresolved_indices[offset:offset + 25]
                chunk = [
                    {"catalogIndex": index, "vendorItemId": str(catalog[index].get("vendorItemId") or "")}
                    for index in indices
                ]
                chunk_rows = await fetch_chunk(
                    page,
                    chunk,
                    concurrency=1,
                    base_delay_ms=450,
                    retries=5,
                )
                for row in chunk_rows:
                    index = int(row["catalogIndex"])
                    catalog_row = catalog[index]
                    row["catalogProductId"] = str(catalog_row.get("productId") or "")
                    row["catalogItemId"] = str(catalog_row.get("itemId") or "")
                    row["catalogVendorItemId"] = str(catalog_row.get("vendorItemId") or "")
                    row["catalogSourceName"] = str(catalog_row.get("sourceName") or "")
                    row["catalogProductUrl"] = str(catalog_row.get("productUrl") or "")
                    row["retryRound"] = retry_round
                    rows_by_index[index] = classify(row)
                await asyncio.sleep(1.2)
            print(json.dumps({"retryRound": retry_round, "attempted": len(unresolved_indices)}, ensure_ascii=False), flush=True)

        await browser.close()

    rows = [rows_by_index[index] for index in sorted(rows_by_index)]
    successful = [row for row in rows if row.get("httpStatus") == 200 and int(row.get("apiCode") or 0) == 200]
    exact = [row for row in successful if row.get("publisherExactUnicorn")]
    unresolved = [row for row in rows if not (row.get("httpStatus") == 200 and int(row.get("apiCode") or 0) == 200)]
    publisher_rows = [row for row in successful if row.get("publisherFields")]
    summary = build_summary(catalog, rows)
    summary["elapsedSeconds"] = round(time.time() - started, 2)
    summary["completionRatio"] = summary["successfulCount"] / len(catalog) if catalog else 0

    save_json(output_dir / "summary.json", summary)
    save_json(output_dir / "navigation-log.json", navigation_log)
    save_json(output_dir / "all-detail-results.json", rows)
    save_json(output_dir / "publisher-detail-results.json", publisher_rows)
    save_json(output_dir / "exact-unicorn-products.json", {
        "criterion": "상품상세 attributes에서 필드명이 출판사를 포함하고 값이 공백 정규화 후 정확히 유니콘",
        "count": len(exact),
        "products": exact,
    })
    save_json(output_dir / "unresolved-products.json", {
        "count": len(unresolved),
        "products": unresolved,
    })
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)

    if len(rows) != len(catalog):
        raise SystemExit(f"row count mismatch: {len(rows)} != {len(catalog)}")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
