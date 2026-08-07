#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import time
from io import BytesIO
from pathlib import Path
from typing import Any

import pytesseract
import requests
from PIL import Image, ImageEnhance, ImageOps

BASE_URL = "https://shop.coupang.com"
LISTING_URL = f"{BASE_URL}/api/v1/listing"
SELLER_ID = "A00214628"
STORE_ID = 79545
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


def payload(page: int) -> dict[str, Any]:
    return {
        "storeId": STORE_ID,
        "brandId": 0,
        "vendorId": SELLER_ID,
        "outboundShippingPlaceId": OUTBOUND_SHIPPING_PLACE_ID,
        "sourceProductId": SOURCE_PRODUCT_ID,
        "sourceVendorItemId": SOURCE_VENDOR_ITEM_ID,
        "source": SOURCE,
        "enableAdultItemDisplay": True,
        "nextPageKey": page,
        "filter": "SORT_KEY:POPULARITY",
        "query": "유니콘",
    }


def fetch_page(page: int) -> dict[str, Any]:
    last = ""
    for attempt in range(1, 5):
        try:
            response = requests.post(LISTING_URL, headers=HEADERS, json=payload(page), timeout=35)
            response.raise_for_status()
            body = response.json()
            if int(body.get("code") or 0) != 200:
                raise RuntimeError(f"API {body.get('code')}: {body.get('msg')}")
            return body.get("data") or {}
        except Exception as exc:
            last = f"{type(exc).__name__}: {exc}"
            time.sleep(attempt)
    raise RuntimeError(last)


def normalize_cdn(raw: str) -> str:
    value = (raw or "").replace("\\/", "/").replace("&amp;", "&")
    if value.startswith("//"):
        value = "https:" + value
    if not value.startswith("http") or "coupangcdn.com" not in value:
        return ""
    match = re.search(r"/thumbnails/remote/(?:[^/]+/)?image/(.+)$", value)
    if match:
        return "https://image1.coupangcdn.com/image/" + match.group(1).split("?")[0]
    return value.split("?")[0]


def collect_candidates() -> list[dict[str, Any]]:
    first = fetch_page(0)
    total = int(first.get("totalCount") or 0)
    pages = max(1, (total + 19) // 20)
    raw_products = list(first.get("products") or [])
    for page in range(1, pages):
        raw_products.extend(fetch_page(page).get("products") or [])
    found: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in raw_products:
        product_id = str(raw.get("productId") or "")
        item_id = str(raw.get("itemId") or "")
        if not product_id or not item_id:
            continue
        area = raw.get("imageAndTitleArea") or {}
        urls = []
        for key in ("completeHttpUrl", "defaultUrl"):
            url = normalize_cdn(str(area.get(key) or ""))
            if url:
                urls.append(url)
        for key in ("completeHttpDetailImageUrls", "detailImageUrls"):
            for raw_url in area.get(key) or []:
                url = normalize_cdn(str(raw_url or ""))
                if url:
                    urls.append(url)
        found[(product_id, item_id)] = {
            "productId": product_id,
            "itemId": item_id,
            "vendorItemId": str(raw.get("vendorItemId") or ""),
            "coupangUniqueId": f"{product_id} - {item_id}",
            "productName": str(area.get("title") or "").strip(),
            "imageUrls": list(dict.fromkeys(urls))[:15],
        }
    return sorted(found.values(), key=lambda row: (int(row["productId"]), int(row["itemId"])))


def clean_value(value: str) -> str:
    value = re.sub(r"\s+", " ", value or "").strip()
    return value.strip(" :：|,·ㆍ/-")


def extract_publisher(text: str) -> tuple[str, str]:
    normalized = text.replace("\r", "\n")
    patterns = (
        r"저자\s*[,·/ㆍ]?\s*출판사\s*[:：]?\s*([^\n|]{1,100})",
        r"저자\s*출판사\s*[:：]?\s*([^\n|]{1,100})",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized, re.I)
        if match:
            value = clean_value(re.split(r"제조|크기|쪽수|배송|교환|반품|발행", match.group(1))[0])[:100]
            if value:
                return value, clean_value(normalized[max(0, match.start() - 150):match.end() + 350])
    return "", ""


def ocr_image(raw: bytes) -> str:
    image = Image.open(BytesIO(raw)).convert("L")
    if image.width < 1400:
        scale = min(3.0, 1400 / max(1, image.width))
        image = image.resize((int(image.width * scale), int(image.height * scale)))
    variants = [image, ImageOps.autocontrast(image), ImageEnhance.Contrast(ImageOps.autocontrast(image)).enhance(1.8)]
    texts: list[str] = []
    for variant in variants:
        for psm in (6, 11):
            try:
                texts.append(pytesseract.image_to_string(variant, lang="kor+eng", config=f"--psm {psm}"))
            except Exception:
                texts.append(pytesseract.image_to_string(variant, lang="eng", config=f"--psm {psm}"))
    return "\n".join(texts)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shard", type=int, required=True)
    parser.add_argument("--shards", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    candidates = collect_candidates()
    selected = [row for index, row in enumerate(candidates) if index % args.shards == args.shard]
    session = requests.Session()
    session.headers.update({"User-Agent": HEADERS["User-Agent"], "Accept-Language": HEADERS["Accept-Language"]})
    results = []
    for index, product in enumerate(selected, 1):
        publisher_values: list[str] = []
        evidence: list[str] = []
        downloaded = 0
        processed = 0
        errors = []
        for image_no, url in enumerate(product.get("imageUrls") or [], 1):
            try:
                response = session.get(url, timeout=25)
                ctype = (response.headers.get("content-type") or "").lower()
                if response.status_code != 200 or "image" not in ctype or not (500 <= len(response.content) <= 20_000_000):
                    continue
                downloaded += 1
                text = ocr_image(response.content)
                processed += 1
                value, snippet = extract_publisher(text)
                if value:
                    publisher_values.append(value)
                    evidence.append(snippet)
            except Exception as exc:
                errors.append(f"{image_no}:{type(exc).__name__}:{exc}"[:300])
        compact_values = [re.sub(r"\s+", "", clean_value(value)) for value in publisher_values]
        exact = any(value == "유니콘" for value in compact_values)
        row = {
            **product,
            "publisherValues": publisher_values,
            "publisherExactUnicorn": exact,
            "publisherEvidence": evidence[:20],
            "imagesAttempted": len(product.get("imageUrls") or []),
            "imagesDownloaded": downloaded,
            "imagesProcessed": processed,
            "errors": errors[:20],
        }
        results.append(row)
        print(json.dumps({"shard": args.shard, "processed": index, "total": len(selected), "uid": product["coupangUniqueId"], "exactUnicorn": exact, "publisherValues": publisher_values}, ensure_ascii=False), flush=True)

    summary = {
        "shard": args.shard,
        "candidateTotal": len(candidates),
        "processed": len(results),
        "exactUnicorn": sum(bool(row.get("publisherExactUnicorn")) for row in results),
        "publisherEvidence": sum(bool(row.get("publisherValues")) for row in results),
        "noImages": sum(not row.get("imageUrls") for row in results),
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: summary[key] for key in ("shard", "candidateTotal", "processed", "exactUnicorn", "publisherEvidence", "noImages")}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
