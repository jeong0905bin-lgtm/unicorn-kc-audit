#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import requests

BASE = "https://shop.coupang.com"
SCRIPT = "https://front.coupangcdn.com/coupang-store-display/20260324160003_kr/f6ae536.js"
SELLER_ID = "A00214628"
STORE_ID = 79545
KNOWN = {
    "productId": "8411161016",
    "itemId": "24319968314",
    "vendorItemId": "91335726263",
}
STORE_URL = f"{BASE}/{SELLER_ID}"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.7",
    "Content-Type": "application/json",
    "Origin": BASE,
    "Referer": STORE_URL,
}
ROUTES = [
    "/api/v2/store/individualInfo/product",
    "/api/v2/store/individualInfo/products",
]


def save(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def hits(value, path="$", out=None):
    if out is None:
        out = []
    if isinstance(value, dict):
        for key, child in value.items():
            hits(child, f"{path}.{key}", out)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            hits(child, f"{path}[{index}]", out)
    else:
        text = str(value or "")
        if any(token.lower() in text.lower() for token in ("유니콘", "저자", "출판사", "kc", "cert", "8411161016", "24319968314", "91335726263")):
            out.append({"path": path, "value": text[:1000]})
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    session = requests.Session()
    session.headers.update(HEADERS)
    result = {"script": SCRIPT, "contexts": {}, "attempts": []}
    js = session.get(SCRIPT, timeout=40)
    result["scriptStatus"] = js.status_code
    result["scriptLength"] = len(js.content)
    text = js.text
    for route in ROUTES:
        positions = [match.start() for match in re.finditer(re.escape(route), text)]
        result["contexts"][route] = [
            text[max(0, position - 2500):min(len(text), position + 5000)]
            for position in positions[:5]
        ]

    exact_vi = {
        "vendorItemIds": [KNOWN["vendorItemId"]],
        "isVIBased": True,
        "storeId": STORE_ID,
        "vendorId": SELLER_ID,
        "ignoreAdultCheck": True,
        "source": "brandstore_sdp_atf",
        "pageType": "STORE",
    }
    payloads = [
        {"id": "exact-js-vi", "body": exact_vi},
        {"id": "exact-js-vi-minimal", "body": {"vendorItemIds": [KNOWN["vendorItemId"]], "isVIBased": True, "storeId": STORE_ID, "vendorId": SELLER_ID, "ignoreAdultCheck": True}},
        {"id": "singular-vi", "body": {"vendorItemId": KNOWN["vendorItemId"], "isVIBased": True, "storeId": STORE_ID, "vendorId": SELLER_ID, "ignoreAdultCheck": True}},
        {"id": "pi-array", "body": {"productIds": [KNOWN["productId"]], "isVIBased": False, "storeId": STORE_ID, "vendorId": SELLER_ID, "ignoreAdultCheck": True}},
        {"id": "pi-singular", "body": {"productId": KNOWN["productId"], "isVIBased": False, "storeId": STORE_ID, "vendorId": SELLER_ID, "ignoreAdultCheck": True}},
    ]

    for route in ROUTES:
        url = BASE + route
        for spec in payloads:
            row = {"route": route, "method": "POST", "payloadId": spec["id"], "payload": spec["body"]}
            try:
                response = session.post(url, json=spec["body"], timeout=30)
                row.update({
                    "status": response.status_code,
                    "contentType": response.headers.get("content-type", ""),
                    "length": len(response.content),
                    "url": response.url,
                    "bodyPrefix": response.text[:1000],
                })
                if response.ok:
                    try:
                        data = response.json()
                    except Exception as exc:
                        row["jsonError"] = f"{type(exc).__name__}: {exc}"
                    else:
                        row["jsonHits"] = hits(data)[:500]
                        row["topLevelKeys"] = sorted(data.keys()) if isinstance(data, dict) else []
                        row["jsonPreview"] = data
            except Exception as exc:
                row["error"] = f"{type(exc).__name__}: {exc}"
            result["attempts"].append(row)

    save(args.output, result)
    print(json.dumps({
        "scriptStatus": result["scriptStatus"],
        "contextCounts": {key: len(value) for key, value in result["contexts"].items()},
        "attempts": [{key: row.get(key) for key in ("route", "payloadId", "status", "length", "bodyPrefix")} for row in result["attempts"]],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
