#!/usr/bin/env python3
from __future__ import annotations

import json
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


def normalize(raw: dict[str, Any]) -> dict[str, Any] | None:
    product_id = str(raw.get("productId") or "")
    item_id = str(raw.get("itemId") or "")
    if not product_id or not item_id:
        return None
    area = raw.get("imageAndTitleArea") or {}
    detail_urls = list(area.get("completeHttpDetailImageUrls") or area.get("detailImageUrls") or [])
    return {
        "productId": product_id,
        "itemId": item_id,
        "vendorItemId": str(raw.get("vendorItemId") or ""),
        "coupangUniqueId": f"{product_id} - {item_id}",
        "productName": str(area.get("title") or "").strip(),
        "mainImageUrl": str(area.get("completeHttpUrl") or area.get("defaultUrl") or ""),
        "detailImageUrls": detail_urls,
    }


def main() -> None:
    out = Path("work/brand_probe")
    out.mkdir(parents=True, exist_ok=True)
    reports = []
    for variant in FILTER_VARIANTS:
        try:
            first = fetch(0, variant)
            sample = [row for raw in first["products"] if (row := normalize(raw))]
            reports.append({
                "filter": variant,
                "ok": True,
                "totalCount": first["totalCount"],
                "validCount": first["validCount"],
                "sampleCount": len(sample),
                "sample": sample,
            })
        except Exception as exc:
            reports.append({"filter": variant, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
    usable = [r for r in reports if r.get("ok") and int(r.get("totalCount") or 0) > 0]
    result = {"unicornBrandFilterId": UNICORN_BRAND_FILTER_ID, "usable": usable, "reports": reports}
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False), flush=True)
    if not usable:
        raise SystemExit("No Unicorn brand-filter variant returned products")


if __name__ == "__main__":
    main()
