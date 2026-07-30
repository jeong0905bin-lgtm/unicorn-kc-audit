#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any

import requests

BASE_URL = "https://shop.coupang.com"
LISTING_URL = f"{BASE_URL}/api/v1/listing"
SELLER_ID = "A00214628"
STORE_ID = 79545
BRAND_ID = 0
UNICORN_BRAND_FILTER_ID = 6295
OUTBOUND_SHIPPING_PLACE_ID = 1208642
SOURCE_PRODUCT_ID = 9402620761
SOURCE_VENDOR_ITEM_ID = 94889588242
SOURCE = "brandstore_sdp_atf"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/150.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.7",
    "Content-Type": "application/json",
    "Origin": BASE_URL,
    "Referer": f"{BASE_URL}/{SELLER_ID}",
}

FILTER_VARIANTS = [
    f"BRAND_KEY:{UNICORN_BRAND_FILTER_ID}|SORT_KEY:POPULARITY",
    f"SORT_KEY:POPULARITY|BRAND_KEY:{UNICORN_BRAND_FILTER_ID}",
    f"BRAND:{UNICORN_BRAND_FILTER_ID}|SORT_KEY:POPULARITY",
    f"SORT_KEY:POPULARITY|BRAND:{UNICORN_BRAND_FILTER_ID}",
]


def payload(page: int, filter_value: str) -> dict[str, Any]:
    return {
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


def fetch(page: int, filter_value: str) -> dict[str, Any]:
    last_error = ""
    for attempt in range(1, 5):
        try:
            response = requests.post(LISTING_URL, headers=HEADERS, json=payload(page, filter_value), timeout=35)
            response.raise_for_status()
            body = response.json()
            if int(body.get("code") or 0) != 200:
                raise RuntimeError(f"API {body.get('code')}: {body.get('msg')}")
            data = body.get("data") or {}
            return {
                "page": page,
                "totalCount": int(data.get("totalCount") or 0),
                "validCount": int(data.get("validCount") or 0),
                "products": list(data.get("products") or []),
            }
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(attempt)
    raise RuntimeError(last_error)


def normalize_url(value: str) -> str:
    value = str(value or "")
    return "https:" + value if value.startswith("//") else value


def normalize(raw: dict[str, Any]) -> dict[str, Any] | None:
    product_id = str(raw.get("productId") or "")
    item_id = str(raw.get("itemId") or "")
    vendor_item_id = str(raw.get("vendorItemId") or "")
    if not product_id or not item_id:
        return None
    area = raw.get("imageAndTitleArea") or {}
    detail_urls = [normalize_url(x) for x in (area.get("completeHttpDetailImageUrls") or area.get("detailImageUrls") or []) if x]
    product_url = f"https://www.coupang.com/vp/products/{product_id}?itemId={item_id}"
    if vendor_item_id:
        product_url += f"&vendorItemId={vendor_item_id}"
    return {
        "productId": product_id,
        "itemId": item_id,
        "vendorItemId": vendor_item_id,
        "coupangUniqueId": f"{product_id} - {item_id}",
        "productUrl": product_url,
        "sourceName": str(area.get("title") or "").strip(),
        "mainImageUrl": normalize_url(area.get("completeHttpUrl") or area.get("defaultUrl") or ""),
        "detailImageUrls": detail_urls,
        "sourcePartitions": [f"brand:{UNICORN_BRAND_FILTER_ID}"],
    }


def main() -> None:
    out = Path("work/brand_probe")
    out.mkdir(parents=True, exist_ok=True)
    reports = []
    selected_filter = ""
    selected_total = 0
    for variant in FILTER_VARIANTS:
        try:
            first = fetch(0, variant)
            sample = [row for raw in first["products"] if (row := normalize(raw))]
            report = {
                "filter": variant,
                "ok": True,
                "totalCount": first["totalCount"],
                "validCount": first["validCount"],
                "sampleCount": len(sample),
                "sample": sample,
            }
            reports.append(report)
            if not selected_filter and 0 < first["totalCount"] < 1000 and "BRAND_KEY" in variant:
                selected_filter = variant
                selected_total = first["totalCount"]
        except Exception as exc:
            reports.append({"filter": variant, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
    if not selected_filter:
        raise SystemExit("No usable BRAND_KEY Unicorn filter returned a bounded candidate set")

    products: dict[tuple[str, str], dict[str, Any]] = {}
    pages = max(1, math.ceil(selected_total / 20))
    page_reports = []
    for page in range(pages):
        result = fetch(page, selected_filter)
        page_reports.append({"page": page, "totalCount": result["totalCount"], "rows": len(result["products"])})
        for raw in result["products"]:
            row = normalize(raw)
            if row:
                products[(row["productId"], row["itemId"])] = row
    rows = sorted(products.values(), key=lambda row: (int(row["productId"]), int(row["itemId"])))
    candidate_result = {
        "sellerId": SELLER_ID,
        "brandFilterId": UNICORN_BRAND_FILTER_ID,
        "selectedFilter": selected_filter,
        "reportedTotal": selected_total,
        "count": len(rows),
        "products": rows,
    }
    result = {
        "unicornBrandFilterId": UNICORN_BRAND_FILTER_ID,
        "selectedFilter": selected_filter,
        "reportedTotal": selected_total,
        "collected": len(rows),
        "pages": page_reports,
        "reports": reports,
    }
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "candidates.json").write_text(json.dumps(candidate_result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"filter": selected_filter, "reportedTotal": selected_total, "collected": len(rows)}, ensure_ascii=False), flush=True)
    if len(rows) != selected_total:
        raise SystemExit(f"Incomplete Unicorn brand candidates: {len(rows)}/{selected_total}")


if __name__ == "__main__":
    main()
