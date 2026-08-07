#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote_plus, urlparse
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup

SELLER_NAME = "북프렌즈"
EXACT_PUBLISHER_RE = re.compile(r"저자\s*,\s*출판사\s*(?:\||:|：|-)?\s*유니콘")
PRODUCT_NUMBER_RE = re.compile(r"쿠팡상품번호\s*[:：]?\s*(\d+)\s*[-–—]\s*(\d+)")
URL_PRODUCT_RE = re.compile(r"/vp/products/(\d+)")
KC_RE = re.compile(r"\b(?:CB|CA|CR|R-R|B\d{4}|SU|YU|XU|U\d{3})[A-Z0-9-]{5,}\b", re.I)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.7,en;q=0.6",
}


def clean(value: Any) -> str:
    value = html.unescape(str(value or ""))
    return re.sub(r"\s+", " ", value).strip()


def fetch(session: requests.Session, url: str, attempts: int = 3) -> requests.Response:
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = session.get(url, headers=HEADERS, timeout=(10, 25), allow_redirects=True)
            if response.status_code in {429, 500, 502, 503, 504}:
                raise RuntimeError(f"HTTP {response.status_code}")
            return response
        except Exception as exc:
            last = exc
            if attempt < attempts:
                time.sleep(attempt * 1.5 + random.random())
    raise RuntimeError(repr(last))


def bing_rss(query: str, first: int) -> list[dict[str, str]]:
    session = requests.Session()
    url = f"https://www.bing.com/search?format=rss&count=50&first={first}&q={quote_plus(query)}"
    response = fetch(session, url)
    if response.status_code != 200:
        raise RuntimeError(f"Bing HTTP {response.status_code}: {response.text[:150]}")
    root = ElementTree.fromstring(response.content)
    rows = []
    for item in root.findall(".//item"):
        rows.append({
            "engine": "bing-rss",
            "query": query,
            "title": clean(item.findtext("title")),
            "url": clean(item.findtext("link")),
            "snippet": clean(item.findtext("description")),
        })
    return rows


def duckduckgo(query: str) -> list[dict[str, str]]:
    session = requests.Session()
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    response = fetch(session, url)
    if response.status_code != 200:
        raise RuntimeError(f"DDG HTTP {response.status_code}: {response.text[:150]}")
    soup = BeautifulSoup(response.text, "html.parser")
    rows = []
    for result in soup.select(".result"):
        link = result.select_one(".result__a")
        snippet = result.select_one(".result__snippet")
        if not link:
            continue
        rows.append({
            "engine": "duckduckgo-html",
            "query": query,
            "title": clean(link.get_text(" ")),
            "url": clean(link.get("href")),
            "snippet": clean(snippet.get_text(" ") if snippet else ""),
        })
    return rows


def result_identity(row: dict[str, str]) -> tuple[str, str] | None:
    text = f"{row.get('title','')} {row.get('snippet','')}"
    m = PRODUCT_NUMBER_RE.search(text)
    if m:
        return m.group(1), m.group(2)

    url = row.get("url") or ""
    pm = URL_PRODUCT_RE.search(url)
    if not pm:
        return None
    product_id = pm.group(1)
    qs = parse_qs(urlparse(url).query)
    item_id = (qs.get("itemId") or [""])[0]
    return (product_id, item_id) if item_id else None


def assess(row: dict[str, str], catalog_by_key: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    joined = clean(f"{row.get('title','')} | {row.get('snippet','')}")
    identity = result_identity(row)
    catalog = catalog_by_key.get(identity) if identity else None
    kcs = []
    for value in KC_RE.findall(joined.upper()):
        value = value.upper().rstrip(".,;:)")
        if value == "U003E1577-7011":
            continue
        if value not in kcs:
            kcs.append(value)
    return {
        **row,
        "joined": joined,
        "exactPublisherEvidence": bool(EXACT_PUBLISHER_RE.search(joined)),
        "identity": list(identity) if identity else None,
        "currentCatalogMatch": bool(catalog),
        "catalogProduct": catalog,
        "kcNumbersFromSnippet": kcs,
    }


def build_queries(candidates: list[dict[str, Any]]) -> list[str]:
    queries = [
        'site:coupang.com/vp/products "판매자:북프렌즈" "저자, 출판사" "유니콘"',
        'site:coupang.com/vp/products "북프렌즈" "저자, 출판사 | 유니콘"',
        'site:coupang.com/vp/products "107-27-94844" "저자, 출판사" "유니콘"',
        'site:coupang.com/vp/products "저자, 출판사" "유니콘" "쿠팡상품번호"',
    ]
    # Candidate-specific queries are discovery-only. Final acceptance still requires exact disclosure evidence.
    for product in candidates:
        pid = product.get("productId")
        iid = product.get("itemId")
        title = clean(product.get("sourceName"))
        queries.append(f'"{pid}" "{iid}" "저자, 출판사"')
        if title:
            compact_title = title[:65]
            queries.append(f'"{compact_title}" "저자, 출판사"')
    return list(dict.fromkeys(queries))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    catalog_data = json.loads(args.catalog.read_text(encoding="utf-8"))
    candidate_data = json.loads(args.candidates.read_text(encoding="utf-8"))
    catalog = catalog_data.get("products") or []
    candidates = candidate_data.get("products") or []
    catalog_by_key = {(str(p["productId"]), str(p["itemId"])): p for p in catalog}
    queries = build_queries(candidates)

    raw_results: list[dict[str, str]] = []
    failures: list[dict[str, str]] = []

    tasks = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for query in queries[:4]:
            for first in (1, 51, 101):
                tasks.append((f"bing:{first}", query, pool.submit(bing_rss, query, first)))
            tasks.append(("ddg", query, pool.submit(duckduckgo, query)))
        # Candidate-specific exact searches. One Bing RSS request each.
        for query in queries[4:]:
            tasks.append(("bing:1", query, pool.submit(bing_rss, query, 1)))

        for label, query, future in tasks:
            try:
                raw_results.extend(future.result())
            except Exception as exc:
                failures.append({"source": label, "query": query, "error": f"{type(exc).__name__}: {exc}"})

    dedup: dict[tuple[str, str, str], dict[str, str]] = {}
    for row in raw_results:
        key = (row.get("url", ""), row.get("title", ""), row.get("snippet", ""))
        dedup[key] = row
    assessed = [assess(row, catalog_by_key) for row in dedup.values()]

    exact_current = [r for r in assessed if r["exactPublisherEvidence"] and r["currentCatalogMatch"]]
    exact_external = [r for r in assessed if r["exactPublisherEvidence"] and not r["currentCatalogMatch"]]

    by_product: dict[tuple[str, str], dict[str, Any]] = {}
    for row in exact_current:
        identity = tuple(row["identity"])
        entry = by_product.setdefault(identity, {
            "productId": identity[0],
            "itemId": identity[1],
            "coupangUniqueId": f"{identity[0]} - {identity[1]}",
            "productName": (row.get("catalogProduct") or {}).get("sourceName", ""),
            "vendorItemId": (row.get("catalogProduct") or {}).get("vendorItemId", ""),
            "kcNumbersFromSnippets": [],
            "evidence": [],
        })
        for kc in row.get("kcNumbersFromSnippet") or []:
            if kc not in entry["kcNumbersFromSnippets"]:
                entry["kcNumbersFromSnippets"].append(kc)
        entry["evidence"].append({
            "engine": row.get("engine"),
            "query": row.get("query"),
            "title": row.get("title"),
            "url": row.get("url"),
            "snippet": row.get("snippet"),
        })

    summary = {
        "catalogReportedTotal": catalog_data.get("reportedTotal"),
        "catalogCount": len(catalog),
        "candidateCount": len(candidates),
        "queryCount": len(queries),
        "rawResultCount": len(raw_results),
        "dedupResultCount": len(assessed),
        "exactCurrentProductCount": len(by_product),
        "exactCurrentProducts": sorted(by_product.values(), key=lambda p: (int(p["productId"]), int(p["itemId"]))),
        "exactExternalEvidenceCount": len(exact_external),
        "failures": failures,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("catalogCount", "candidateCount", "queryCount", "rawResultCount", "exactCurrentProductCount")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
