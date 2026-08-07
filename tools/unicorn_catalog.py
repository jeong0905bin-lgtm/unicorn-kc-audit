#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import requests

SELLER_ID = "A00214628"
STORE_ID = 79545
BRAND_ID = 0
OUTBOUND_SHIPPING_PLACE_ID = 1208642
SOURCE_PRODUCT_ID = 9402620761
SOURCE_VENDOR_ITEM_ID = 94889588242
SOURCE = "brandstore_sdp_atf"

BASE_URL = "https://shop.coupang.com"
LISTING_URL = f"{BASE_URL}/api/v1/listing"
MAIN_CATEGORY_URL = f"{BASE_URL}/api/v1/main_category"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.7",
    "Content-Type": "application/json",
    "Origin": BASE_URL,
    "Referer": (
        f"{BASE_URL}/{SELLER_ID}?source={SOURCE}"
        f"&ocid={OUTBOUND_SHIPPING_PLACE_ID}"
        f"&checkBatchDelivery=true"
        f"&pid={SOURCE_PRODUCT_ID}"
        f"&viid={SOURCE_VENDOR_ITEM_ID}"
        "&platform=p&brandId=0&btcEnableForce=false"
    ),
}

SORT_VALUES = ("POPULARITY", "LOW_PRICE", "HIGH_PRICE", "BEST_SELLING", "NEW")
CATEGORY_FILTER_TEMPLATES = (
    "CATEGORY:{category}|SORT_KEY:POPULARITY",
    "SORT_KEY:POPULARITY|CATEGORY:{category}",
    "CATEGORY:{category}@VENDOR|SORT_KEY:POPULARITY",
)


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def request_json(url: str, payload: dict[str, Any], attempts: int = 5) -> dict[str, Any]:
    last_error = ""
    for attempt in range(1, attempts + 1):
        try:
            response = requests.post(url, headers=HEADERS, json=payload, timeout=35)
            if response.status_code != 200:
                raise RuntimeError(f"HTTP {response.status_code}: {response.text[:300]}")
            data = response.json()
            if int(data.get("code") or 0) != 200:
                raise RuntimeError(f"API {data.get('code')}: {data.get('msg') or data.get('message')}")
            return data
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < attempts:
                time.sleep(min(8, attempt * 1.5))
    raise RuntimeError(last_error)


def listing_payload(page: int, filter_value: str, query: str = "") -> dict[str, Any]:
    payload: dict[str, Any] = {
        "storeId": STORE_ID,
        "brandId": BRAND_ID,
        "vendorId": SELLER_ID,
        "outboundShippingPlaceId": OUTBOUND_SHIPPING_PLACE_ID,
        "sourceProductId": SOURCE_PRODUCT_ID,
        "sourceVendorItemId": SOURCE_VENDOR_ITEM_ID,
        "source": SOURCE,
        "enableAdultItemDisplay": True,
        "nextPageKey": page,
        "filter": filter_value,
    }
    if query:
        payload["query"] = query
    return payload


def fetch_listing(page: int, filter_value: str, query: str = "") -> dict[str, Any]:
    response = request_json(LISTING_URL, listing_payload(page, filter_value, query))
    data = response.get("data") or {}
    return {
        "page": page,
        "filter": filter_value,
        "query": query,
        "totalCount": int(data.get("totalCount") or 0),
        "validCount": int(data.get("validCount") or 0),
        "searchId": str(data.get("searchId") or ""),
        "products": list(data.get("products") or []),
    }


def fetch_categories() -> list[dict[str, Any]]:
    response = request_json(
        MAIN_CATEGORY_URL,
        {"vendorId": SELLER_ID, "brandId": BRAND_ID, "categoryLevel": 3},
    )
    categories = list((response.get("data") or {}).get("categories") or [])
    return [
        {"id": int(item["id"]), "name": str(item.get("name") or "")}
        for item in categories
        if item.get("id")
    ]


def product_row(raw: dict[str, Any], source_partition: str) -> dict[str, Any] | None:
    product_id = str(raw.get("productId") or "")
    item_id = str(raw.get("itemId") or "")
    vendor_item_id = str(raw.get("vendorItemId") or "")
    if not product_id or not item_id:
        return None
    image_area = raw.get("imageAndTitleArea") or {}
    title = re.sub(r"\s+", " ", str(image_area.get("title") or "")).strip()
    image = str(image_area.get("completeHttpUrl") or image_area.get("defaultUrl") or "")
    if image.startswith("//"):
        image = "https:" + image
    product_url = f"https://www.coupang.com/vp/products/{product_id}?itemId={item_id}"
    if vendor_item_id:
        product_url += f"&vendorItemId={vendor_item_id}"
    return {
        "productId": product_id,
        "itemId": item_id,
        "vendorItemId": vendor_item_id,
        "coupangUniqueId": f"{product_id} - {item_id}",
        "productUrl": product_url,
        "sourceName": title,
        "mainImageUrl": image,
        "valid": bool(raw.get("valid", True)),
        "soldOut": bool((raw.get("soldoutArea") or {}).get("soldout", False)),
        "sourcePartitions": [source_partition],
    }


def merge_product(target: dict[tuple[str, str], dict[str, Any]], raw: dict[str, Any], partition: str) -> None:
    row = product_row(raw, partition)
    if not row:
        return
    key = (row["productId"], row["itemId"])
    old = target.get(key)
    if old is None:
        target[key] = row
        return
    partitions = list(dict.fromkeys((old.get("sourcePartitions") or []) + [partition]))
    old["sourcePartitions"] = partitions
    if not old.get("vendorItemId") and row.get("vendorItemId"):
        old["vendorItemId"] = row["vendorItemId"]
        old["productUrl"] = row["productUrl"]
    if len(row.get("sourceName") or "") > len(old.get("sourceName") or ""):
        old["sourceName"] = row["sourceName"]
    if not old.get("mainImageUrl") and row.get("mainImageUrl"):
        old["mainImageUrl"] = row["mainImageUrl"]


def choose_category_filter(category_id: int) -> tuple[str, dict[str, Any]]:
    errors: list[str] = []
    for template in CATEGORY_FILTER_TEMPLATES:
        value = template.format(category=category_id)
        try:
            first = fetch_listing(0, value)
        except Exception as exc:
            errors.append(f"{value}: {type(exc).__name__}: {exc}")
            continue
        if first["totalCount"] > 0 or first["products"]:
            return value, first
    raise RuntimeError(f"category {category_id} had no usable filter; " + " | ".join(errors))


def fetch_partition_pages(
    partition_name: str,
    filter_value: str,
    first: dict[str, Any],
    max_pages: int = 50,
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    total = int(first.get("totalCount") or 0)
    pages = min(max_pages, max(1, math.ceil(total / 20))) if total else 1
    results: list[dict[str, Any]] = [first]
    if pages > 1:
        with ThreadPoolExecutor(max_workers=min(8, pages - 1)) as pool:
            futures = {
                pool.submit(fetch_listing, page, filter_value): page
                for page in range(1, pages)
            }
            for future in as_completed(futures):
                results.append(future.result())
    results.sort(key=lambda item: item["page"])
    meta = {
        "partition": partition_name,
        "filter": filter_value,
        "reportedTotal": total,
        "pagesRequested": pages,
        "rowsReturned": sum(len(item["products"]) for item in results),
        "emptyPages": [item["page"] for item in results if not item["products"]],
    }
    return partition_name, results, meta


def collect_query(query: str) -> dict[str, Any]:
    first = fetch_listing(0, "SORT_KEY:POPULARITY", query=query)
    total = int(first["totalCount"])
    pages = min(50, max(1, math.ceil(total / 20))) if total else 1
    results = [first]
    for page in range(1, pages):
        results.append(fetch_listing(page, "SORT_KEY:POPULARITY", query=query))
    found: dict[tuple[str, str], dict[str, Any]] = {}
    for page in results:
        for raw in page["products"]:
            merge_product(found, raw, f"query:{query}")
    rows = sorted(found.values(), key=lambda p: (int(p["productId"]), int(p["itemId"])))
    return {
        "query": query,
        "reportedTotal": total,
        "count": len(rows),
        "products": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--diagnostics", type=Path, required=True)
    parser.add_argument("--candidate-output", type=Path, required=True)
    args = parser.parse_args()

    root_first = fetch_listing(0, "SORT_KEY:POPULARITY")
    expected_total = int(root_first["totalCount"])
    if expected_total < 1500:
        raise SystemExit(f"catalog sanity check failed: reported total {expected_total}")

    categories = fetch_categories()
    if len(categories) < 10:
        raise SystemExit(f"category sanity check failed: {len(categories)} categories")

    partitions: list[tuple[str, str, dict[str, Any]]] = []
    partitions.append(("root:POPULARITY", "SORT_KEY:POPULARITY", root_first))
    for sort_value in SORT_VALUES:
        if sort_value == "POPULARITY":
            continue
        first = fetch_listing(0, f"SORT_KEY:{sort_value}")
        partitions.append((f"root:{sort_value}", f"SORT_KEY:{sort_value}", first))

    category_probe_errors = []
    for category in categories:
        try:
            filter_value, first = choose_category_filter(category["id"])
            partitions.append((f"category:{category['id']}:{category['name']}", filter_value, first))
        except Exception as exc:
            category_probe_errors.append({
                "category": category,
                "error": f"{type(exc).__name__}: {exc}",
            })

    collected: dict[tuple[str, str], dict[str, Any]] = {}
    partition_meta: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            pool.submit(fetch_partition_pages, name, filter_value, first): name
            for name, filter_value, first in partitions
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                partition_name, pages, meta = future.result()
                partition_meta.append(meta)
                for page in pages:
                    for raw in page["products"]:
                        merge_product(collected, raw, partition_name)
                print(json.dumps({
                    "partition": partition_name,
                    "reportedTotal": meta["reportedTotal"],
                    "rowsReturned": meta["rowsReturned"],
                    "catalogUnique": len(collected),
                }, ensure_ascii=False), flush=True)
            except Exception as exc:
                failures.append({"partition": name, "error": f"{type(exc).__name__}: {exc}"})

    products = sorted(collected.values(), key=lambda p: (int(p["productId"]), int(p["itemId"])))
    candidate_result = collect_query("유니콘")

    diagnostics = {
        "sellerId": SELLER_ID,
        "storeId": STORE_ID,
        "reportedTotal": expected_total,
        "collectedUnique": len(products),
        "coverageRatio": (len(products) / expected_total) if expected_total else 0,
        "categoryCount": len(categories),
        "categories": categories,
        "categoryProbeErrors": category_probe_errors,
        "partitionCount": len(partitions),
        "partitions": sorted(partition_meta, key=lambda item: item["partition"]),
        "partitionFailures": failures,
        "unicornQueryReportedTotal": candidate_result["reportedTotal"],
        "unicornQueryUnique": candidate_result["count"],
    }
    save_json(args.output, {
        "sellerId": SELLER_ID,
        "storeId": STORE_ID,
        "reportedTotal": expected_total,
        "count": len(products),
        "products": products,
    })
    save_json(args.diagnostics, diagnostics)
    save_json(args.candidate_output, candidate_result)

    print(json.dumps({
        "reportedTotal": expected_total,
        "collectedUnique": len(products),
        "categoryCount": len(categories),
        "unicornQueryUnique": candidate_result["count"],
        "failures": len(failures) + len(category_probe_errors),
    }, ensure_ascii=False), flush=True)

    if failures or category_probe_errors:
        raise SystemExit("one or more catalog partitions failed; see diagnostics")
    if len(products) < expected_total:
        raise SystemExit(
            f"incomplete catalog: collected {len(products)} of reported {expected_total}"
        )


if __name__ == "__main__":
    main()
