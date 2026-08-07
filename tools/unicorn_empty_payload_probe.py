#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests
from playwright.async_api import async_playwright

BASE = "https://shop.coupang.com"
SELLER_ID = "A00214628"
STORE_ID = 79545
OUTBOUND_SHIPPING_PLACE_ID = 1208642
SOURCE_PRODUCT_ID = 9402620761
SOURCE_VENDOR_ITEM_ID = 94889588242
SOURCE = "brandstore_sdp_atf"
LISTING = f"{BASE}/api/v1/listing"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.7",
    "Content-Type": "application/json",
    "Origin": BASE,
    "Referer": f"{BASE}/{SELLER_ID}?source={SOURCE}&ocid={OUTBOUND_SHIPPING_PLACE_ID}&pid={SOURCE_PRODUCT_ID}&viid={SOURCE_VENDOR_ITEM_ID}&platform=p",
}
WRAPPER_FIELDS = (
    "sourceFeedId", "sourceSearchId", "clickEventId", "lptag", "spec", "src",
    "wPcid", "pageType", "source", "productListRules",
)


def save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def listing_payload(query: str, page: int = 0) -> dict[str, Any]:
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
        "query": query,
    }


def request_listing(session: requests.Session, query: str) -> dict[str, Any]:
    response = session.post(LISTING, headers=HEADERS, json=listing_payload(query), timeout=45)
    text = response.text
    parsed = None
    try:
        parsed = response.json()
    except Exception:
        pass
    return {
        "query": query,
        "status": response.status_code,
        "length": len(response.content),
        "prefix": text[:1000],
        "json": parsed,
    }


def product_title(row: dict[str, Any]) -> str:
    return str(row.get("catalogSourceName") or row.get("sourceName") or "").strip()


def choose_samples(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    empty = [row for row in products if row.get("httpStatus") == 200]
    denied = [row for row in products if row.get("httpStatus") == 403]
    selected: list[dict[str, Any]] = []
    known_ids = {"91335726263", "5521875355", "5207492637"}
    for row in products:
        if str(row.get("catalogVendorItemId") or "") in known_ids:
            selected.append(row)
    for group, count in ((empty, 20), (denied, 12)):
        preferred = [row for row in group if "유니콘" in product_title(row)]
        ordinary = [row for row in group if row not in preferred]
        for row in (preferred + ordinary)[:count]:
            if row not in selected:
                selected.append(row)
    return selected[:35]


def parse_link_fields(link: str) -> dict[str, Any]:
    if not link:
        return {}
    query = parse_qs(urlparse(link).query)
    aliases = {
        "sourceType": "source",
        "searchId": "sourceSearchId",
    }
    output: dict[str, Any] = {}
    for key, values in query.items():
        if not values:
            continue
        target = aliases.get(key, key)
        if target in WRAPPER_FIELDS:
            output[target] = values[0]
    return output


def metadata_candidates(raw: dict[str, Any] | None, search_id: str) -> list[tuple[str, dict[str, Any]]]:
    raw = raw or {}
    image = raw.get("imageAndTitleArea") or {}
    link = str(raw.get("link") or image.get("link") or "")
    direct = {key: raw.get(key) for key in WRAPPER_FIELDS if raw.get(key) not in (None, "")}
    nested = {}
    for container_name in ("tracking", "trackingInfo", "analytics", "eventInfo", "productListRules"):
        container = raw.get(container_name)
        if isinstance(container, dict):
            for key in WRAPPER_FIELDS:
                if container.get(key) not in (None, ""):
                    nested[key] = container[key]
    link_fields = parse_link_fields(link)
    if search_id and "sourceSearchId" not in direct:
        direct["sourceSearchId"] = search_id
    candidates = [
        ("minimal", {}),
        ("raw-direct", direct),
        ("raw-link", {**direct, **link_fields}),
        ("raw-nested", {**direct, **nested, **link_fields}),
        ("known-source", {**direct, **nested, **link_fields, "source": SOURCE, "pageType": "STORE"}),
        ("brandstore-sdp", {**direct, **nested, **link_fields, "source": "brandstore_sdp_atf", "pageType": "BRANDSTORE_SDP"}),
    ]
    deduped: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    for name, value in candidates:
        clean = {key: child for key, child in value.items() if child not in (None, "", [], {})}
        token = json.dumps(clean, ensure_ascii=False, sort_keys=True)
        if token not in seen:
            seen.add(token)
            deduped.append((name, clean))
    return deduped


async def browser_post(page, route: str, body: dict[str, Any]) -> dict[str, Any]:
    return await page.evaluate(
        """async ({route, body}) => {
          const controller = new AbortController();
          const timer = setTimeout(() => controller.abort(), 25000);
          try {
            const response = await fetch(route, {
              method:'POST', credentials:'include', signal:controller.signal,
              headers:{'content-type':'application/json','accept':'application/json, text/plain, */*'},
              body:JSON.stringify(body)
            });
            const text = await response.text();
            let parsed = null; try { parsed = JSON.parse(text); } catch (_) {}
            return {
              status:response.status,
              contentType:response.headers.get('content-type') || '',
              length:text.length,
              prefix:text.slice(0,1600),
              code:parsed ? parsed.code : null,
              message:parsed ? (parsed.msg || parsed.message || null) : null,
              dataPresent:Boolean(parsed && parsed.data),
              topLevelKeys:parsed && typeof parsed === 'object' ? Object.keys(parsed).sort() : []
            };
          } catch (error) {
            return {status:null,length:0,error:`${error?.name || 'Error'}: ${error?.message || String(error)}`};
          } finally { clearTimeout(timer); }
        }""",
        {"route": route, "body": body},
    )


async def main_async() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--unresolved", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    unresolved = json.loads(args.unresolved.read_text(encoding="utf-8"))
    samples = choose_samples(list(unresolved.get("products") or []))
    session = requests.Session()
    metadata_rows: list[dict[str, Any]] = []

    for sample in samples:
        title = product_title(sample)
        queries = [title, re.sub(r"\s+", " ", title)[:28], str(sample.get("catalogProductId") or "")]
        query_results = []
        matches = []
        for query in dict.fromkeys(query for query in queries if query):
            result = request_listing(session, query)
            parsed = result.get("json") or {}
            data = parsed.get("data") or {}
            result["searchId"] = str(data.get("searchId") or "")
            products = list(data.get("products") or [])
            result["productCount"] = len(products)
            query_results.append({key: result.get(key) for key in ("query", "status", "length", "prefix", "searchId", "productCount")})
            for raw in products:
                if str(raw.get("vendorItemId") or "") == str(sample.get("catalogVendorItemId") or "") or (
                    str(raw.get("productId") or "") == str(sample.get("catalogProductId") or "")
                    and str(raw.get("itemId") or "") == str(sample.get("catalogItemId") or "")
                ):
                    matches.append({"query": query, "searchId": result["searchId"], "raw": raw})
            if matches:
                break
        metadata_rows.append({"sample": sample, "queries": query_results, "matches": matches})

    result: dict[str, Any] = {"sampleCount": len(samples), "metadataRows": metadata_rows, "attempts": []}
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        context = await browser.new_context(
            locale="ko-KR", timezone_id="Asia/Seoul", viewport={"width": 1440, "height": 1000},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
        )
        page = await context.new_page()
        try:
            response = await page.goto(f"{BASE}/{SELLER_ID}", wait_until="domcontentloaded", timeout=90000)
            result["navigation"] = {"status": response.status if response else None, "url": page.url, "title": await page.title()}
        except Exception as exc:
            result["navigation"] = {"error": f"{type(exc).__name__}: {exc}", "url": page.url}

        for metadata in metadata_rows:
            sample = metadata["sample"]
            matches = metadata["matches"] or [{"query": "", "searchId": "", "raw": None}]
            match = matches[0]
            vendor_item_id = str(sample.get("catalogVendorItemId") or "")
            for variant, extra in metadata_candidates(match.get("raw"), str(match.get("searchId") or "")):
                common = {
                    "vendorItemIds": [vendor_item_id],
                    "isVIBased": True,
                    "storeId": STORE_ID,
                    "vendorId": SELLER_ID,
                    "ignoreAdultCheck": True,
                    **extra,
                }
                route_bodies = [
                    ("/api/v2/store/individualInfo/product", common),
                    ("/api/v2/store/individualInfo/products", common),
                ]
                for route, body in route_bodies:
                    response_row = await browser_post(page, route, body)
                    result["attempts"].append({
                        "catalogIndex": sample.get("catalogIndex"),
                        "productId": sample.get("catalogProductId"),
                        "itemId": sample.get("catalogItemId"),
                        "vendorItemId": vendor_item_id,
                        "sourceName": product_title(sample),
                        "previousStatus": sample.get("httpStatus"),
                        "metadataFound": bool(metadata["matches"]),
                        "route": route,
                        "variant": variant,
                        "extra": extra,
                        **response_row,
                    })
                    if response_row.get("dataPresent"):
                        break
                if result["attempts"][-1].get("dataPresent"):
                    break
                await page.wait_for_timeout(220)
        await browser.close()

    successes = [row for row in result["attempts"] if row.get("dataPresent")]
    result["summary"] = {
        "sampleCount": len(samples),
        "metadataMatchCount": sum(1 for row in metadata_rows if row["matches"]),
        "attemptCount": len(result["attempts"]),
        "http200Count": sum(1 for row in result["attempts"] if row.get("status") == 200),
        "nonEmptyCount": sum(1 for row in result["attempts"] if (row.get("length") or 0) > 0),
        "dataSuccessCount": len(successes),
        "dataSuccessProducts": sorted({str(row.get("vendorItemId")) for row in successes}),
    }
    save(args.output, result)
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
