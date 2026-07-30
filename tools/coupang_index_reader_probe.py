#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/150.0.0.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.7",
}

CASES = [
    {
        "label": "control-indexed-book",
        "productId": "6714090858",
        "itemId": "15596758830",
        "vendorItemId": "3234653644",
        "title": "REST API DESIGN RULEBOOK",
        "expectedPublisher": "Masse, Mark",
        "categoryIds": [458443],
        "legacyItemIds": [],
    },
    {
        "label": "known-unicorn-target",
        "productId": "8411161016",
        "itemId": "24319968314",
        "vendorItemId": "91335726263",
        "title": "위시캣 스티커퀸 300",
        "expectedPublisher": "유니콘",
        "categoryIds": [349237, 347211, 326768],
        "legacyItemIds": ["27355912643"],
    },
    {
        "label": "unicorn-brand-target",
        "productId": "9237088992",
        "itemId": "24365968915",
        "vendorItemId": "92128043110",
        "title": "프린세스 캐치티니핑 스티커퀸300",
        "expectedPublisher": "유니콘",
        "categoryIds": [349237, 347211, 326768],
        "legacyItemIds": [],
    },
]


def norm(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def compact(value: str) -> str:
    return re.sub(r"[\s,·ㆍ/|:：\-]+", "", norm(value))


def product_urls(case: dict) -> list[tuple[str, str]]:
    product_id = case["productId"]
    item_id = case["itemId"]
    vendor_item_id = case["vendorItemId"]
    rows: list[tuple[str, str]] = [
        (
            "desktop-current-item",
            f"https://www.coupang.com/vp/products/{product_id}?itemId={item_id}&vendorItemId={vendor_item_id}",
        ),
        ("desktop-product-only", f"https://www.coupang.com/vp/products/{product_id}"),
        (
            "mobile-current-item",
            f"https://m.coupang.com/vm/products/{product_id}?itemId={item_id}&vendorItemId={vendor_item_id}",
        ),
    ]
    for category_id in case.get("categoryIds") or []:
        rows.append((
            f"desktop-category-{category_id}",
            f"https://www.coupang.com/vp/products/{product_id}?categoryId={category_id}"
            f"&itemId={item_id}&sourceType=CATEGORY&vendorItemId={vendor_item_id}",
        ))
    for legacy_item in case.get("legacyItemIds") or []:
        rows.append((
            f"desktop-legacy-item-{legacy_item}",
            f"https://www.coupang.com/vp/products/{product_id}?itemId={legacy_item}&vendorItemId={vendor_item_id}",
        ))
    return list(dict.fromkeys(rows))


def publisher_values(text: str) -> list[str]:
    clean = norm(text)
    values: list[str] = []
    patterns = [
        r"저자\s*,\s*출판사\s*[|:]?\s*([^|\n]{1,160})",
        r"저자\s*·\s*출판사\s*[|:]?\s*([^|\n]{1,160})",
        r"저자\s*출판사\s*[|:]?\s*([^|\n]{1,160})",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, clean, re.I):
            value = norm(match.group(1))
            value = re.split(
                r"크기\s*\(|쪽수|제품\s*구성|발행일|필수\s*표기|상품상세|책소개|배송",
                value,
                maxsplit=1,
            )[0].strip(" |,:：")
            if value and value not in values:
                values.append(value[:160])
    return values


def snippets(text: str, terms: tuple[str, ...] = ("저자, 출판사", "저자", "유니콘", "access denied")) -> list[str]:
    clean = norm(text)
    low = clean.lower()
    out: list[str] = []
    for term in terms:
        pos = low.find(term.lower())
        if pos >= 0:
            item = clean[max(0, pos - 300):pos + 1000]
            if item not in out:
                out.append(item)
    return out[:8]


def fetch(url: str, *, params: dict | None = None, attempts: int = 3) -> dict:
    last_error = ""
    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(url, params=params, headers=HEADERS, timeout=45)
            return {
                "ok": response.status_code == 200,
                "status": response.status_code,
                "finalUrl": response.url,
                "contentType": response.headers.get("content-type", ""),
                "text": response.text,
                "error": "",
            }
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(attempt)
    return {"ok": False, "status": 0, "finalUrl": url, "contentType": "", "text": "", "error": last_error}


def parse_bing_rss(raw: str) -> list[dict]:
    soup = BeautifulSoup(raw, "xml")
    rows = []
    for item in soup.find_all("item"):
        rows.append({
            "title": norm(item.title.get_text(" ", strip=True) if item.title else ""),
            "link": norm(item.link.get_text(" ", strip=True) if item.link else ""),
            "description": norm(item.description.get_text(" ", strip=True) if item.description else ""),
        })
    return rows


def parse_html_results(raw: str) -> list[dict]:
    soup = BeautifulSoup(raw, "html.parser")
    rows = []
    for selector in ("li.b_algo", ".result", "article"):
        for block in soup.select(selector):
            link = block.select_one("a[href]")
            if not link:
                continue
            rows.append({
                "title": norm(link.get_text(" ", strip=True)),
                "link": link.get("href") or "",
                "description": norm(block.get_text(" ", strip=True)),
            })
        if rows:
            break
    return rows


def relevant_rows(rows: list[dict], product_id: str) -> list[dict]:
    out = []
    for row in rows:
        joined = "\n".join(str(row.get(k) or "") for k in ("title", "link", "description"))
        if product_id in joined or "coupang.com/vp/products" in joined:
            item = dict(row)
            item["publisherValues"] = publisher_values(joined)
            item["snippets"] = snippets(joined)
            out.append(item)
    return out[:20]


def probe_case(case: dict) -> dict:
    query_variants = [
        f'site:coupang.com/vp/products/{case["productId"]} "저자, 출판사"',
        f'"{case["productId"]} - {case["itemId"]}" "저자, 출판사"',
        f'"{case["title"]}" "저자, 출판사" 쿠팡',
    ]
    result = {**case, "productUrls": product_urls(case), "routes": []}

    for variant, url in result["productUrls"]:
        jina = fetch("https://r.jina.ai/" + url)
        jina_text = jina.pop("text", "")
        result["routes"].append({
            "route": "jina-reader",
            "variant": variant,
            "sourceUrl": url,
            **jina,
            "length": len(jina_text),
            "publisherValues": publisher_values(jina_text),
            "snippets": snippets(jina_text),
        })

    for query in query_variants:
        rss = fetch("https://www.bing.com/search", params={"q": query, "format": "rss"})
        rss_text = rss.pop("text", "")
        rss_rows = parse_bing_rss(rss_text) if rss.get("status") == 200 else []
        result["routes"].append({
            "route": "bing-rss", "query": query, **rss, "length": len(rss_text),
            "resultCount": len(rss_rows), "relevant": relevant_rows(rss_rows, case["productId"]),
        })

        bing = fetch("https://www.bing.com/search", params={"q": query, "setlang": "ko"})
        bing_text = bing.pop("text", "")
        bing_rows = parse_html_results(bing_text) if bing.get("status") == 200 else []
        result["routes"].append({
            "route": "bing-html", "query": query, **bing, "length": len(bing_text),
            "resultCount": len(bing_rows), "relevant": relevant_rows(bing_rows, case["productId"]),
        })

        ddg = fetch("https://html.duckduckgo.com/html/", params={"q": query})
        ddg_text = ddg.pop("text", "")
        ddg_rows = parse_html_results(ddg_text) if ddg.get("status") == 200 else []
        result["routes"].append({
            "route": "duckduckgo-html", "query": query, **ddg, "length": len(ddg_text),
            "resultCount": len(ddg_rows), "relevant": relevant_rows(ddg_rows, case["productId"]),
        })

    found_values: list[str] = []
    for route in result["routes"]:
        for value in route.get("publisherValues") or []:
            if value not in found_values:
                found_values.append(value)
        for row in route.get("relevant") or []:
            for value in row.get("publisherValues") or []:
                if value not in found_values:
                    found_values.append(value)
    result["publisherValues"] = found_values
    expected = compact(case["expectedPublisher"])
    result["expectedExact"] = any(compact(value) == expected for value in found_values)
    result["unicornExact"] = any(compact(value) == "유니콘" for value in found_values)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    results = []
    for case in CASES:
        row = probe_case(case)
        results.append(row)
        print(json.dumps({
            "label": row["label"], "publisherValues": row["publisherValues"],
            "expectedExact": row["expectedExact"], "unicornExact": row["unicornExact"],
        }, ensure_ascii=False), flush=True)
    summary = {
        "cases": results,
        "controlConfirmed": bool(results and results[0]["expectedExact"]),
        "targetUnicornConfirmed": sum(bool(row["unicornExact"]) for row in results[1:]),
        "viable": bool(results and results[0]["expectedExact"]),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("controlConfirmed", "targetUnicornConfirmed", "viable")}, ensure_ascii=False), flush=True)
    if not summary["controlConfirmed"]:
        raise SystemExit("indexed-reader routes could not recover the known control publisher")


if __name__ == "__main__":
    main()
