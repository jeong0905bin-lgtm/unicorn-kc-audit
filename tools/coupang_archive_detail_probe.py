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

COMMON_CRAWL_INDEXES = [
    "CC-MAIN-2026-30", "CC-MAIN-2026-25", "CC-MAIN-2026-21",
    "CC-MAIN-2026-17", "CC-MAIN-2026-12", "CC-MAIN-2026-08",
    "CC-MAIN-2026-04", "CC-MAIN-2025-51", "CC-MAIN-2025-47",
    "CC-MAIN-2025-43", "CC-MAIN-2025-38", "CC-MAIN-2025-33",
]

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
    rows: list[str] = []
    for term in ("저자, 출판사", "저자", "유니콘", "access denied"):
        pos = low.find(term.lower())
        if pos >= 0:
            snippet = clean[max(0, pos - 250):pos + 900]
            if snippet not in rows:
                rows.append(snippet)
    return rows[:5]


def get(url: str, *, params: Any = None, timeout: int = 35, attempts: int = 2, headers: dict | None = None) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return requests.get(url, params=params, headers=headers or HEADERS, timeout=timeout)
        except Exception as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(attempt)
    assert last_error is not None
    raise last_error


def query_common_crawl(index_id: str, product_id: str) -> list[dict[str, Any]]:
    response = get(
        f"https://index.commoncrawl.org/{index_id}-index",
        params=[
            ("url", f"www.coupang.com/vp/products/{product_id}"),
            ("matchType", "prefix"),
            ("output", "json"),
            ("filter", "status:200"),
            ("filter", "mime:text/html"),
            ("collapse", "digest"),
            ("limit", "10"),
        ],
        timeout=30,
        attempts=2,
    )
    if response.status_code != 200:
        return []
    rows: list[dict[str, Any]] = []
    for line in response.text.splitlines():
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def fetch_warc(record: dict[str, Any]) -> str:
    offset = int(record["offset"])
    length = int(record["length"])
    response = get(
        "https://data.commoncrawl.org/" + str(record["filename"]),
        headers={**HEADERS, "Range": f"bytes={offset}-{offset + length - 1}"},
        timeout=60,
        attempts=2,
    )
    if response.status_code not in (200, 206):
        return ""
    try:
        for warc in ArchiveIterator(io.BytesIO(response.content)):
            if warc.rec_type in ("response", "resource"):
                return warc.content_stream().read().decode("utf-8", errors="ignore")
    except Exception:
        return ""
    return ""


def probe_common_crawl(product_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index_id in COMMON_CRAWL_INDEXES:
        try:
            records = query_common_crawl(index_id, product_id)
        except Exception as exc:
            rows.append({"index": index_id, "error": f"{type(exc).__name__}: {exc}"})
            continue
        rows.append({"index": index_id, "recordCount": len(records)})
        for record in records[:3]:
            try:
                text = fetch_warc(record)
            except Exception as exc:
                rows.append({"index": index_id, "url": record.get("url"), "error": f"{type(exc).__name__}: {exc}"})
                continue
            values = publisher_values(text)
            rows.append({
                "index": index_id,
                "timestamp": record.get("timestamp"),
                "url": record.get("url"),
                "length": len(text),
                "publisherValues": values,
                "evidence": evidence(text),
            })
            if values:
                return rows
    return rows


def probe_wayback(product_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        response = get(
            "https://web.archive.org/cdx/search/cdx",
            params=[
                ("url", f"www.coupang.com/vp/products/{product_id}"),
                ("matchType", "prefix"),
                ("output", "json"),
                ("filter", "statuscode:200"),
                ("filter", "mimetype:text/html"),
                ("fl", "timestamp,original,statuscode,mimetype,digest"),
                ("collapse", "digest"),
                ("limit", "10"),
                ("from", "2020"),
            ],
            timeout=35,
            attempts=2,
        )
        if response.status_code != 200:
            return [{"status": response.status_code}]
        data = response.json()
        captures = data[1:] if isinstance(data, list) and len(data) > 1 else []
    except Exception as exc:
        return [{"error": f"{type(exc).__name__}: {exc}"}]
    rows.append({"captureCount": len(captures)})
    for capture in captures[:6]:
        timestamp, original, statuscode, mimetype, digest = capture
        try:
            replay = get(
                f"https://web.archive.org/web/{timestamp}id_/{original}",
                timeout=45,
                attempts=2,
            )
            text = replay.text if replay.status_code == 200 else ""
            values = publisher_values(text)
            rows.append({
                "timestamp": timestamp,
                "original": original,
                "status": replay.status_code,
                "length": len(text),
                "publisherValues": values,
                "evidence": evidence(text),
            })
            if values:
                break
        except Exception as exc:
            rows.append({"timestamp": timestamp, "original": original, "error": f"{type(exc).__name__}: {exc}"})
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    results: list[dict[str, Any]] = []
    try:
        for case in CASES:
            cc = probe_common_crawl(case["productId"])
            wb = probe_wayback(case["productId"])
            values: list[str] = []
            for row in cc + wb:
                for value in row.get("publisherValues") or []:
                    if value not in values:
                        values.append(value)
            exact = any(compact(value) == compact(case["expected"]) for value in values)
            result = {**case, "commonCrawl": cc, "wayback": wb, "publisherValues": values, "expectedExact": exact}
            results.append(result)
            print(json.dumps({"label": case["label"], "publisherValues": values, "expectedExact": exact}, ensure_ascii=False), flush=True)
    finally:
        summary = {
            "cases": results,
            "controlConfirmed": bool(results and results[0].get("expectedExact")),
            "targetUnicornConfirmed": sum(bool(row.get("expectedExact")) for row in results[1:]),
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"controlConfirmed": summary["controlConfirmed"], "targetUnicornConfirmed": summary["targetUnicornConfirmed"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
