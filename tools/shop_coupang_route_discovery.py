#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

SELLER_ID = "A00214628"
STORE_ID = 79545
OUTBOUND_SHIPPING_PLACE_ID = 1208642
SOURCE_PRODUCT_ID = 9402620761
SOURCE_VENDOR_ITEM_ID = 94889588242
BASE = "https://shop.coupang.com"
STORE_URL = f"{BASE}/{SELLER_ID}?source=brandstore_sdp_atf&ocid={OUTBOUND_SHIPPING_PLACE_ID}&checkBatchDelivery=true&pid={SOURCE_PRODUCT_ID}&viid={SOURCE_VENDOR_ITEM_ID}&platform=p&brandId=0&btcEnableForce=false"
LISTING_URL = f"{BASE}/api/v1/listing"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.7",
}


def save(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def payload(page: int = 0, query: str = "") -> dict:
    value = {
        "storeId": STORE_ID,
        "brandId": 0,
        "vendorId": SELLER_ID,
        "outboundShippingPlaceId": OUTBOUND_SHIPPING_PLACE_ID,
        "sourceProductId": SOURCE_PRODUCT_ID,
        "sourceVendorItemId": SOURCE_VENDOR_ITEM_ID,
        "source": "brandstore_sdp_atf",
        "enableAdultItemDisplay": True,
        "nextPageKey": page,
        "filter": "SORT_KEY:POPULARITY",
    }
    if query:
        value["query"] = query
    return value


def walk(value, path="$", out=None):
    if out is None:
        out = []
    if isinstance(value, dict):
        for key, child in value.items():
            walk(child, f"{path}.{key}", out)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            walk(child, f"{path}[{index}]", out)
    else:
        text = str(value or "")
        if any(token in text for token in ("유니콘", "저자", "출판사", "KC", "8411161016", "24319968314", "91335726263")):
            out.append({"path": path, "value": text[:500]})
    return out


def extract_routes(text: str, source: str) -> list[dict]:
    rows = []
    patterns = [
        r"[\"']((?:https?:)?//[^\"']+)[\"']",
        r"[\"']((?:/api/|/next-api/|/vp/|/vm/)[A-Za-z0-9_?&=./{}:${}\-]+)[\"']",
    ]
    seen = set()
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            route = match.group(1)
            if len(route) > 500:
                continue
            low = route.lower()
            if not any(token in low for token in ("product", "detail", "item", "vendor", "btf", "essential", "cert", "content", "api")):
                continue
            key = (source, route)
            if key in seen:
                continue
            seen.add(key)
            rows.append({"source": source, "route": route})
    for token in ("vendorItemCertifications", "vendorItemContentDescriptions", "essentials", "next-api/products/btf", "저자, 출판사"):
        start = 0
        while True:
            index = text.find(token, start)
            if index < 0:
                break
            snippet = text[max(0, index - 250): min(len(text), index + 500)]
            key = (source, token, snippet)
            if key not in seen:
                seen.add(key)
                rows.append({"source": source, "token": token, "snippet": snippet})
            start = index + len(token)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    session = requests.Session()
    session.headers.update(HEADERS)
    result = {
        "sellerId": SELLER_ID,
        "storeUrl": STORE_URL,
        "storeStatus": None,
        "scripts": [],
        "routes": [],
        "listingQueries": [],
        "errors": [],
    }

    try:
        response = session.get(STORE_URL, timeout=30)
        result["storeStatus"] = response.status_code
        result["storeLength"] = len(response.content)
        html = response.text
        result["routes"].extend(extract_routes(html, "store-html"))
        soup = BeautifulSoup(html, "html.parser")
        scripts = []
        for tag in soup.find_all("script", src=True):
            url = urljoin(STORE_URL, tag.get("src"))
            if url not in scripts:
                scripts.append(url)
        result["scripts"] = scripts[:100]
        for index, url in enumerate(scripts[:40]):
            try:
                js = session.get(url, timeout=30)
                info = {"url": url, "status": js.status_code, "length": len(js.content)}
                if js.status_code == 200 and len(js.content) <= 12_000_000:
                    routes = extract_routes(js.text, f"script-{index}")
                    info["routeCount"] = len(routes)
                    result["routes"].extend(routes)
                result.setdefault("scriptResults", []).append(info)
            except Exception as exc:
                result["errors"].append({"stage": "script", "url": url, "error": f"{type(exc).__name__}: {exc}"})
    except Exception as exc:
        result["errors"].append({"stage": "store", "error": f"{type(exc).__name__}: {exc}"})

    api_headers = {
        **HEADERS,
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": BASE,
        "Referer": STORE_URL,
    }
    for query in ("위시캣 스티커퀸 300", "8411161016", "유니콘"):
        try:
            response = session.post(LISTING_URL, headers=api_headers, json=payload(0, query), timeout=35)
            item = {"query": query, "status": response.status_code, "length": len(response.content)}
            if response.ok:
                data = response.json()
                products = list((data.get("data") or {}).get("products") or [])
                item["reportedTotal"] = int((data.get("data") or {}).get("totalCount") or 0)
                item["productCount"] = len(products)
                item["matches"] = walk(data)[:200]
                item["productKeySets"] = [sorted(p.keys()) for p in products[:5] if isinstance(p, dict)]
                item["products"] = products[:10]
            else:
                item["bodyPrefix"] = response.text[:500]
            result["listingQueries"].append(item)
        except Exception as exc:
            result["listingQueries"].append({"query": query, "error": f"{type(exc).__name__}: {exc}"})

    dedup = []
    seen = set()
    for row in result["routes"]:
        key = json.dumps(row, ensure_ascii=False, sort_keys=True)
        if key not in seen:
            seen.add(key)
            dedup.append(row)
    result["routes"] = dedup[:3000]
    result["routeCount"] = len(dedup)
    save(args.output, result)
    print(json.dumps({"storeStatus": result.get("storeStatus"), "scripts": len(result.get("scripts") or []), "routeCount": result.get("routeCount"), "listingQueries": [{k:v for k,v in q.items() if k in ('query','status','reportedTotal','productCount')} for q in result["listingQueries"]]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
