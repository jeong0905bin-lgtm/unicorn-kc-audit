#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import io
import json
import re
import time
from pathlib import Path
from typing import Any

import requests
from warcio.archiveiterator import ArchiveIterator

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/150.0.0.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.7",
}

CASES = [
    {"label": "control", "productId": "6714090858", "expected": "Masse, Mark"},
    {"label": "known-unicorn", "productId": "8411161016", "expected": "유니콘"},
    {"label": "unicorn-brand", "productId": "9237088992", "expected": "유니콘"},
]


def norm(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def compact(value: str) -> str:
    return re.sub(r"[\s,·ㆍ/|:：\-]+", "", norm(value))


def publisher_values(text: str) -> list[str]:
    clean = norm(text)
    values: list[str] = []
    for pattern in (
        r"저자\s*,\s*출판사\s*[|:]?\s*([^|\n]{1,180})",
        r"저자\s*·\s*출판사\s*[|:]?\s*([^|\n]{1,180})",
        r"저자\s*출판사\s*[|:]?\s*([^|\n]{1,180})",
    ):
        for match in re.finditer(pattern, clean, re.I):
            value = re.split(
                r"크기\s*\(|쪽수|제품\s*구성|발행일|필수\s*표기|상품상세|책소개|배송",
                norm(match.group(1)),
                maxsplit=1,
            )[0].strip(" |,:：")
            if value and value not in values:
                values.append(value[:180])
    return values


def evidence(text: str) -> list[str]:
    clean = norm(text)
    low = clean.lower()
    rows = []
    for term in ("저자, 출판사", "저자", "유니콘", "access denied"):
        pos = low.find(term.lower())
        if pos >= 0:
            snippet = clean[max(0, pos - 250):pos + 900]
            if snippet not in rows:
                rows.append(snippet)
    return rows[:5]


def get_json(url: str, params: dict[str, Any] | None = None, timeout: int = 45) -> Any:
    response = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
    response.raise_for_status()
    return response.json()


def common_crawl_indexes(limit: int = 12) -> list[str]:
    rows = get_json("https://index.commoncrawl.org/collinfo.json")
    return [str(row["id"]) for row in rows[:limit] if row.get("id")]


def query_common_crawl(index_id: str, product_id: str) -> list[dict[str, Any]]:
    url = f"https://index.commoncrawl.org/{index_id}-index"
    params = {
        "url": f"www.coupang.com/vp/products/{product_id}",
        "matchType": "prefix",
        "output": "json",
        "filter": ["status:200", "mime:text/html"],
        "collapse": "digest",
        "limit": "10",
    }
    response = requests.get(url, params=params, headers=HEADERS, timeout=60)
    if response.status_code != 200:
        return []
    rows = []
    for line in response.text.splitlines():
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def fetch_warc(record: dict[str, Any]) -> str:
    offset = int(record["offset"])
    length = int(record["length"])
    url = "https://data.commoncrawl.org/" + str(record["filename"])
    response = requests.get(
        url,
        headers={**HEADERS, "Range": f"bytes={offset}-{offset + length - 1}"},
        timeout=90,
    )
    if response.status_code not in (200, 206):
        return ""
    try:
        for warc in ArchiveIterator(io.BytesIO(response.content)):
            if warc.rec_type in ("response", "resource"):
                raw = warc.content_stream().read()
                return raw.decode("utf-8", errors="ignore")
    except Exception:
        return ""
    return ""


def probe_common_crawl(product_id: str) -> list[dict[str, Any]]:
    found = []
    for index_id in common_crawl_indexes():
        try:
            records = query_common_crawl(index_id, product_id)
        except Exception as exc:
            found.append({"index": index_id, "error": f"{type(exc).__name__}: {exc}"})
            continue
        for record in records[:4]:
            text = fetch_warc(record)
            values = publisher_values(text)
            found.append({
                "index": index_id,
                "timestamp": record.get("timestamp"),
                "url": record.get("url"),
                "status": record.get("status"),
                "length": len(text),
                "publisherValues": values,
                "evidence": evidence(text),
            })
            if values:
                return found
        if found and any(row.get("publisherValues") for row in found):
            break
    return found


def wayback_records(product_id: str) -> list[list[str]]:
    params = {
        "url": f"www.coupang.com/vp/products/{product_id}",
        "matchType": "prefix",
        "output": "json",
        "filter": ["statuscode:200", "mimetype:text/html"],
        "fl": "timestamp,original,statuscode,mimetype,digest",
        "collapse": "digest",
        "limit": "10",
        "from": "2020",
    }
    response = requests.get(
        "https://web.archive.org/cdx/search/cdx",
        params=params,
        headers=HEADERS,
        timeout=60,
    )
    if response.status_code != 200:
        return []
    data = response.json()
    return data[1:] if isinstance(data, list) and len(data) > 1 else []


def probe_wayback(product_id: str) -> list[dict[str, Any]]:
    rows = []
    try:
        captures = wayback_records(product_id)
    except Exception as exc:
        return [{"error": f"{type(exc).__name__}: {exc}"}]
    for capture in captures[:8]:
        timestamp, original, statuscode, mimetype, digest = capture
        replay = f"https://web.archive.org/web/{timestamp}id_/{original}"
        try:
            response = requests.get(replay, headers=HEADERS, timeout=60)
            text = response.text if response.status_code == 200 else ""
            values = publisher_values(text)
            rows.append({
                "timestamp": timestamp,
                "original": original,
                "status": response.status_code,
                "length": len(text),
                "publisherValues": values,
                "evidence": evidence(text),
            })
            if values:
                break
        except Exception as exc:
            rows.append({"timestamp": timestamp, "original": original, "error": f"{type(exc).__name__}: {exc}"})
        time.sleep(0.5)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    results = []
    for case in CASES:
        cc = probe_common_crawl(case["productId"])
        wb = probe_wayback(case["productId"])
        values = []
        for row in cc + wb:
            for value in row.get("publisherValues") or []:
                if value not in values:
                    values.append(value)
        exact = any(compact(value) == compact(case["expected"]) for value in values)
        result = {**case, "commonCrawl": cc, "wayback": wb, "publisherValues": values, "expectedExact": exact}
        results.append(result)
        print(json.dumps({"label": case["label"], "publisherValues": values, "expectedExact": exact}, ensure_ascii=False), flush=True)
    summary = {
        "cases": results,
        "controlConfirmed": bool(results[0]["expectedExact"]),
        "targetUnicornConfirmed": sum(bool(row["expectedExact"]) for row in results[1:]),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"controlConfirmed": summary["controlConfirmed"], "targetUnicornConfirmed": summary["targetUnicornConfirmed"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
