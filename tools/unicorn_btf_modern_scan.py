#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import random
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any

import requests

BTF_URL = "https://www.coupang.com/next-api/products/btf"
KNOWN = {
    "productId": "8411161016",
    "itemId": "24319968314",
    "vendorItemId": "91335726263",
    "expectedPublisher": "유니콘",
    "expectedKc": "CB064H009-3002",
}

PROFILES = {
    "windows": {
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
        "sec_ch": '"Google Chrome";v="141", "Not?A_Brand";v="8", "Chromium";v="141"',
        "platform": '"Windows"',
    },
    "macos": {
        "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
        "sec_ch": '"Google Chrome";v="141", "Not?A_Brand";v="8", "Chromium";v="141"',
        "platform": '"macOS"',
    },
    "linux": {
        "ua": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
        "sec_ch": '"Google Chrome";v="141", "Not?A_Brand";v="8", "Chromium";v="141"',
        "platform": '"Linux"',
    },
}


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def norm(value: Any) -> str:
    text = re.sub(r"\s+", "", str(value or ""))
    return text.strip(" ,/·ㆍ|:：-")


def walk(value: Any):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def parse_payload(data: Any) -> dict[str, Any]:
    publisher = ""
    publisher_source = ""
    kc_numbers: set[str] = set()
    image_urls: set[str] = set()
    detail_strings = 0

    for node in walk(data):
        if isinstance(node, dict):
            title = norm(node.get("title") or node.get("name") or node.get("label"))
            if title == norm("저자, 출판사"):
                for key in ("description", "value", "content", "text"):
                    candidate = norm(node.get(key))
                    if candidate:
                        publisher = candidate
                        publisher_source = key
                        break
            for key, raw in node.items():
                key_l = str(key).lower()
                if key_l in {"certificationno", "certificationnumber", "certno", "kcnumber"}:
                    code = norm(raw).upper()
                    if re.fullmatch(r"[A-Z]{1,3}\d{2,4}[A-Z]\d{3,4}-\d{4}[A-Z]?", code):
                        kc_numbers.add(code)
        elif isinstance(node, str):
            if "coupangcdn.com" in node:
                for raw in re.findall(r"(?:https?:)?//[^\s\"'<>]+coupangcdn\.com/[^\s\"'<>]+", node, re.I):
                    url = raw if raw.startswith("http") else "https:" + raw
                    image_urls.add(url.replace("\\/", "/").split("?")[0])
            if len(node) < 200000:
                detail_strings += 1
                for code in re.findall(r"\b[A-Z]{1,3}\d{2,4}[A-Z]\d{3,4}-\d{4}[A-Z]?\b", node.upper()):
                    kc_numbers.add(code)

    return {
        "publisherValue": publisher,
        "publisherExactUnicorn": publisher == "유니콘",
        "publisherSource": publisher_source,
        "kcNumbers": sorted(kc_numbers),
        "detailImageUrls": sorted(image_urls),
        "detailStringCount": detail_strings,
    }


def profile_name() -> str:
    raw = os.environ.get("BTF_PROFILE", "linux").lower()
    return raw if raw in PROFILES else "linux"


def make_headers(referer: str, profile: str) -> dict[str, str]:
    p = PROFILES[profile]
    return {
        "accept": "application/json, text/plain, */*",
        "accept-language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "cache-control": "no-cache",
        "pragma": "no-cache",
        "origin": "https://www.coupang.com",
        "referer": referer,
        "priority": "u=1, i",
        "sec-ch-ua": p["sec_ch"],
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": p["platform"],
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "user-agent": p["ua"],
    }


def fetch_btf(product: dict[str, Any], attempts: int = 3) -> dict[str, Any]:
    product_id = str(product.get("productId") or "")
    item_id = str(product.get("itemId") or "")
    vendor_item_id = str(product.get("vendorItemId") or "")
    referer = f"https://www.coupang.com/vp/products/{product_id}?itemId={item_id}&vendorItemId={vendor_item_id}"
    params = {"productId": product_id, "itemId": item_id, "vendorItemId": vendor_item_id}
    profile = profile_name()
    evidence: list[dict[str, Any]] = []

    for attempt in range(1, attempts + 1):
        session = requests.Session()
        headers = make_headers(referer, profile)
        try:
            # Establish ordinary first-party cookies before the same-origin JSON request.
            for warm_url in ("https://www.coupang.com/", referer):
                try:
                    warm = session.get(warm_url, headers={"user-agent": headers["user-agent"], "accept-language": headers["accept-language"]}, timeout=15, allow_redirects=True)
                    evidence.append({"stage": "warm", "url": warm_url, "status": warm.status_code, "length": len(warm.content)})
                except Exception as exc:
                    evidence.append({"stage": "warm", "url": warm_url, "error": f"{type(exc).__name__}: {exc}"})

            query = dict(params)
            query["_fresh"] = f"{int(time.time() * 1000)}-{attempt}-{random.randint(1000,9999)}"
            response = session.get(BTF_URL, params=query, headers=headers, timeout=30, allow_redirects=True)
            record = {
                "stage": "btf",
                "attempt": attempt,
                "status": response.status_code,
                "contentType": response.headers.get("content-type", ""),
                "length": len(response.content),
                "url": response.url,
                "cookieNames": sorted(session.cookies.keys()),
                "bodyPrefix": response.text[:220],
            }
            evidence.append(record)
            if response.ok:
                try:
                    data = response.json()
                except Exception as exc:
                    record["jsonError"] = f"{type(exc).__name__}: {exc}"
                else:
                    parsed = parse_payload(data)
                    return {"requestStatus": "ok", "httpStatus": response.status_code, "parsed": parsed, "evidence": evidence}
        except Exception as exc:
            evidence.append({"stage": "btf", "attempt": attempt, "error": f"{type(exc).__name__}: {exc}"})
        finally:
            session.close()
        time.sleep(min(5.0, 0.8 * attempt))

    return {"requestStatus": "failed", "httpStatus": next((x.get("status") for x in reversed(evidence) if x.get("stage") == "btf" and x.get("status") is not None), None), "parsed": {}, "evidence": evidence}


def sanitized_product(product: dict[str, Any], fetched: dict[str, Any]) -> dict[str, Any]:
    parsed = fetched.get("parsed") or {}
    return {
        "productId": str(product.get("productId") or ""),
        "itemId": str(product.get("itemId") or ""),
        "vendorItemId": str(product.get("vendorItemId") or ""),
        "coupangUniqueId": str(product.get("coupangUniqueId") or f"{product.get('productId')} - {product.get('itemId')}"),
        "sourceName": str(product.get("sourceName") or ""),
        "productUrl": str(product.get("productUrl") or ""),
        "requestStatus": fetched.get("requestStatus"),
        "httpStatus": fetched.get("httpStatus"),
        "publisherValue": parsed.get("publisherValue", ""),
        "publisherExactUnicorn": bool(parsed.get("publisherExactUnicorn")),
        "publisherSource": parsed.get("publisherSource", ""),
        "kcNumbers": parsed.get("kcNumbers") or [],
        "detailImageUrls": parsed.get("detailImageUrls") or [],
        "evidence": fetched.get("evidence") or [],
    }


def command_probe(args: argparse.Namespace) -> None:
    fetched = fetch_btf(KNOWN, attempts=args.attempts)
    parsed = fetched.get("parsed") or {}
    usable = bool(parsed.get("publisherExactUnicorn"))
    output = {
        "runner": os.environ.get("RUNNER_LABEL", "unknown"),
        "profile": profile_name(),
        "known": KNOWN,
        "usable": usable,
        "publisher": parsed.get("publisherValue", ""),
        "kcNumbers": parsed.get("kcNumbers") or [],
        "expectedKcSeen": KNOWN["expectedKc"] in (parsed.get("kcNumbers") or []),
        "result": fetched,
    }
    save_json(args.output, output)
    print(json.dumps({k: output[k] for k in ("runner", "profile", "usable", "publisher", "kcNumbers", "expectedKcSeen")}, ensure_ascii=False))


def command_scan(args: argparse.Namespace) -> None:
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    products = list(catalog.get("products") or [])
    selected = [p for i, p in enumerate(products) if i % args.shards == args.shard]
    rows: list[dict[str, Any]] = []
    for index, product in enumerate(selected, 1):
        fetched = fetch_btf(product, attempts=args.attempts)
        row = sanitized_product(product, fetched)
        rows.append(row)
        if index % 10 == 0 or index == len(selected):
            save_json(args.output.with_suffix(".checkpoint.json"), {"shard": args.shard, "shards": args.shards, "processed": index, "total": len(selected), "products": rows})
        print(json.dumps({"shard": args.shard, "processed": index, "total": len(selected), "http": row["httpStatus"], "publisher": row["publisherValue"]}, ensure_ascii=False), flush=True)
        time.sleep(args.delay)
    save_json(args.output, {"shard": args.shard, "shards": args.shards, "catalogCount": len(products), "count": len(rows), "products": rows})


def command_merge(args: argparse.Namespace) -> None:
    rows: list[dict[str, Any]] = []
    shard_files = sorted(args.input_dir.glob("shard-*.json"))
    for path in shard_files:
        data = json.loads(path.read_text(encoding="utf-8"))
        rows.extend(data.get("products") or [])
    unique = {(str(r.get("productId")), str(r.get("itemId"))): r for r in rows}
    merged = sorted(unique.values(), key=lambda r: (int(r["productId"]), int(r["itemId"])))
    if len(merged) != args.expected:
        raise SystemExit(f"incomplete merged catalog: {len(merged)}/{args.expected}; shard files={len(shard_files)}")
    exact = [r for r in merged if r.get("publisherExactUnicorn")]
    status_counts = Counter(str(r.get("requestStatus") or "unknown") for r in merged)
    http_counts = Counter(str(r.get("httpStatus")) for r in merged)
    publisher_counts = Counter("exact_unicorn" if r.get("publisherExactUnicorn") else ("other" if r.get("publisherValue") else "missing") for r in merged)
    unique_kc = sorted({code for r in exact for code in (r.get("kcNumbers") or [])})
    summary = {
        "sellerId": "A00214628",
        "catalogCount": len(merged),
        "acceptanceRule": "mandatory disclosure row 저자, 출판사 normalizes exactly to 유니콘",
        "exactUnicornCount": len(exact),
        "exactProductsWithKc": sum(bool(r.get("kcNumbers")) for r in exact),
        "uniqueKcNumbers": unique_kc,
        "requestStatusCounts": dict(status_counts),
        "httpStatusCounts": dict(http_counts),
        "publisherCounts": dict(publisher_counts),
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    save_json(args.output_dir / "summary.json", summary)
    save_json(args.output_dir / "all-products-sanitized.json", {"count": len(merged), "products": merged})
    save_json(args.output_dir / "exact-unicorn-products.json", {"count": len(exact), "products": exact})
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("probe")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--attempts", type=int, default=3)
    p.set_defaults(func=command_probe)

    s = sub.add_parser("scan")
    s.add_argument("--catalog", type=Path, required=True)
    s.add_argument("--shard", type=int, required=True)
    s.add_argument("--shards", type=int, required=True)
    s.add_argument("--output", type=Path, required=True)
    s.add_argument("--attempts", type=int, default=2)
    s.add_argument("--delay", type=float, default=0.15)
    s.set_defaults(func=command_scan)

    m = sub.add_parser("merge")
    m.add_argument("--input-dir", type=Path, required=True)
    m.add_argument("--output-dir", type=Path, required=True)
    m.add_argument("--expected", type=int, default=2230)
    m.set_defaults(func=command_merge)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
