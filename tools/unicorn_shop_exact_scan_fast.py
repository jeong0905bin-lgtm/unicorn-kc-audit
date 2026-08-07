#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

BASE = "https://shop.coupang.com"
SELLER_ID = "A00214628"
STORE_ID = 79545
ENDPOINT = "/api/v2/store/individualInfo/product"


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def norm_label(value: Any) -> str:
    return re.sub(r"[\s,，·ㆍ:：/\\()\[\]_-]+", "", str(value or ""))


def norm_value(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def enrich(row: dict[str, Any], catalog_row: dict[str, Any]) -> dict[str, Any]:
    attrs = row.get("attributes") if isinstance(row.get("attributes"), list) else []
    publisher = [
        {
            "id": item.get("id"),
            "name": str(item.get("name") or ""),
            "value": str(item.get("value") or ""),
            "expose": item.get("expose"),
        }
        for item in attrs
        if isinstance(item, dict) and "출판사" in norm_label(item.get("name"))
    ]
    exact = [item for item in publisher if norm_value(item.get("value")) == "유니콘"]
    row.update({
        "catalogProductId": str(catalog_row.get("productId") or ""),
        "catalogItemId": str(catalog_row.get("itemId") or ""),
        "catalogVendorItemId": str(catalog_row.get("vendorItemId") or ""),
        "catalogSourceName": str(catalog_row.get("sourceName") or ""),
        "catalogProductUrl": str(catalog_row.get("productUrl") or ""),
        "publisherFields": publisher,
        "publisherExactMatches": exact,
        "publisherExactUnicorn": bool(exact),
    })
    return row


async def run_batch(page, batch: list[dict[str, Any]], concurrency: int, timeout_ms: int, retries: int, delay_ms: int):
    return await page.evaluate(
        """async ({batch, endpoint, storeId, vendorId, concurrency, timeoutMs, retries, delayMs}) => {
          const sleep = ms => new Promise(r => setTimeout(r, ms));
          const scanSignals = data => {
            const out = [];
            const seen = new Set();
            const walk = (v, p, depth) => {
              if (v == null || depth > 8 || out.length >= 100) return;
              if (Array.isArray(v)) { v.slice(0,100).forEach((x,i)=>walk(x,`${p}[${i}]`,depth+1)); return; }
              if (typeof v === 'object') {
                for (const [k,x] of Object.entries(v)) {
                  const np = `${p}.${k}`;
                  if (/kc|cert|safety|auth|인증|안전/i.test(k)) {
                    let text; try { text = typeof x === 'string' ? x : JSON.stringify(x); } catch { text = String(x); }
                    const token = `${np}:${text}`;
                    if (!seen.has(token)) { seen.add(token); out.push({path:np,value:text.slice(0,1500)}); }
                  }
                  walk(x,np,depth+1);
                }
                return;
              }
              const text = String(v);
              if (/\b[A-Z]{1,4}\d{2,}[A-Z]?\d*(?:-\d+)+\b/i.test(text)) {
                const token = `${p}:${text}`;
                if (!seen.has(token)) { seen.add(token); out.push({path:p,value:text.slice(0,1500)}); }
              }
            };
            walk(data,'$',0);
            return out;
          };
          const one = async item => {
            let last = null;
            for (let attempt=1; attempt<=retries; attempt++) {
              const controller = new AbortController();
              const timer = setTimeout(()=>controller.abort(), timeoutMs);
              try {
                const response = await fetch(endpoint, {
                  method:'POST', credentials:'include', signal:controller.signal,
                  headers:{'content-type':'application/json','accept':'application/json, text/plain, */*'},
                  body:JSON.stringify({
                    vendorItemIds:[item.vendorItemId], isVIBased:true,
                    storeId, vendorId, ignoreAdultCheck:true
                  })
                });
                const text = await response.text();
                let parsed = null; try { parsed = JSON.parse(text); } catch {}
                const data = parsed && parsed.data && typeof parsed.data === 'object' ? parsed.data : null;
                last = {
                  catalogIndex:item.catalogIndex,
                  requestedVendorItemId:item.vendorItemId,
                  httpStatus:response.status,
                  responseLength:text.length,
                  apiCode:parsed ? parsed.code : null,
                  apiMessage:parsed ? (parsed.msg || parsed.message || null) : null,
                  responsePrefix:text.slice(0,500),
                  productId:data ? String(data.productId || '') : '',
                  itemId:data ? String(data.itemId || '') : '',
                  vendorItemId:data ? String(data.vendorItemId || item.vendorItemId || '') : String(item.vendorItemId || ''),
                  sourceName:data && data.imageAndTitleArea ? String(data.imageAndTitleArea.title || '') : '',
                  groupTitle:data && data.imageAndTitleArea ? String(data.imageAndTitleArea.groupTitle || '') : '',
                  mainImageUrl:data && data.imageAndTitleArea ? String(data.imageAndTitleArea.completeHttpUrl || data.imageAndTitleArea.defaultUrl || '') : '',
                  detailImageUrls:data && data.imageAndTitleArea ? (data.imageAndTitleArea.completeHttpDetailImageUrls || data.imageAndTitleArea.detailImageUrls || []) : [],
                  attributes:data && Array.isArray(data.attributes) ? data.attributes : [],
                  valid:data ? data.valid : null,
                  adult:data ? data.adult : null,
                  productLink:data ? String(data.link || '') : '',
                  kcSignals:data ? scanSignals(data) : [],
                  attempt
                };
                if (response.status === 200 && parsed && Number(parsed.code) === 200 && data) return last;
                if (![403,408,425,429,500,502,503,504].includes(response.status)) return last;
              } catch (error) {
                last = {
                  catalogIndex:item.catalogIndex, requestedVendorItemId:item.vendorItemId,
                  httpStatus:null, error:`${error?.name || 'Error'}: ${error?.message || String(error)}`, attempt
                };
              } finally { clearTimeout(timer); }
              await sleep(Math.min(5000, delayMs * Math.pow(2, attempt-1) + Math.floor(Math.random()*200)));
            }
            return last;
          };
          const results = new Array(batch.length);
          let cursor = 0;
          const worker = async () => {
            while (true) {
              const i = cursor++;
              if (i >= batch.length) return;
              results[i] = await one(batch[i]);
              await sleep(delayMs + Math.floor(Math.random()*Math.max(20,delayMs)));
            }
          };
          await Promise.all(Array.from({length:Math.max(1,concurrency)}, worker));
          return results;
        }""",
        {
            "batch": batch,
            "endpoint": ENDPOINT,
            "storeId": STORE_ID,
            "vendorId": SELLER_ID,
            "concurrency": concurrency,
            "timeoutMs": timeout_ms,
            "retries": retries,
            "delayMs": delay_ms,
        },
    )


def is_success(row: dict[str, Any]) -> bool:
    return row.get("httpStatus") == 200 and int(row.get("apiCode") or 0) == 200


def summary(catalog_count: int, rows: list[dict[str, Any]], started: float) -> dict[str, Any]:
    good = [r for r in rows if is_success(r)]
    unresolved = [r for r in rows if not is_success(r)]
    exact = [r for r in good if r.get("publisherExactUnicorn")]
    publisher = [r for r in good if r.get("publisherFields")]
    statuses: dict[str, int] = {}
    for row in rows:
        key = str(row.get("httpStatus"))
        statuses[key] = statuses.get(key, 0) + 1
    return {
        "sellerId": SELLER_ID,
        "storeId": STORE_ID,
        "catalogCount": catalog_count,
        "processedCount": len(rows),
        "successfulCount": len(good),
        "unresolvedCount": len(unresolved),
        "publisherFieldCount": len(publisher),
        "publisherExactUnicornCount": len(exact),
        "httpStatusCounts": statuses,
        "elapsedSeconds": round(time.time() - started, 2),
    }


async def main_async() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--chunk-size", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=8)
    args = parser.parse_args()

    catalog_doc = json.loads(args.catalog.read_text(encoding="utf-8"))
    catalog = list(catalog_doc.get("products") or [])
    if len(catalog) < 1500:
        raise SystemExit(f"catalog too small: {len(catalog)}")
    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    started = time.time()
    by_index: dict[int, dict[str, Any]] = {}
    navigations: list[dict[str, Any]] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        context = await browser.new_context(
            locale="ko-KR", timezone_id="Asia/Seoul", viewport={"width":1440,"height":1000},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
        )
        page = await context.new_page()
        for url in (f"{BASE}/{SELLER_ID}", BASE):
            try:
                response = await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                navigations.append({"requested":url,"status":response.status if response else None,"url":page.url,"title":await page.title()})
            except Exception as exc:
                navigations.append({"requested":url,"error":f"{type(exc).__name__}: {exc}","url":page.url})
            if page.url.startswith(BASE):
                break

        items = []
        for index, product in enumerate(catalog):
            vi = str(product.get("vendorItemId") or "")
            if vi:
                items.append({"catalogIndex":index,"vendorItemId":vi})
            else:
                by_index[index] = enrich({"catalogIndex":index,"httpStatus":None,"error":"missing vendorItemId","attributes":[]}, product)

        for start in range(0, len(items), args.chunk_size):
            batch = items[start:start+args.chunk_size]
            try:
                rows = await run_batch(page, batch, args.concurrency, 15_000, 2, 60)
            except Exception as exc:
                rows = [{"catalogIndex":item["catalogIndex"],"requestedVendorItemId":item["vendorItemId"],"httpStatus":None,"error":f"batch: {type(exc).__name__}: {exc}","attributes":[]} for item in batch]
            for row in rows:
                index = int(row["catalogIndex"])
                by_index[index] = enrich(row, catalog[index])
            ordered = [by_index[i] for i in sorted(by_index)]
            state = summary(len(catalog), ordered, started)
            save_json(out / "checkpoint.json", {"summary":state,"navigations":navigations,"rows":ordered})
            print(json.dumps(state, ensure_ascii=False), flush=True)
            await asyncio.sleep(0.35)

        unresolved_indices = [i for i,row in by_index.items() if not is_success(row) and str(catalog[i].get("vendorItemId") or "")]
        if unresolved_indices:
            await page.close()
            page = await context.new_page()
            try:
                response = await page.goto(BASE, wait_until="domcontentloaded", timeout=60_000)
                navigations.append({"requested":BASE,"status":response.status if response else None,"url":page.url,"title":await page.title(),"phase":"retry"})
            except Exception as exc:
                navigations.append({"requested":BASE,"error":f"{type(exc).__name__}: {exc}","phase":"retry"})
            for start in range(0, len(unresolved_indices), 50):
                indices = unresolved_indices[start:start+50]
                batch = [{"catalogIndex":i,"vendorItemId":str(catalog[i].get("vendorItemId") or "")} for i in indices]
                rows = await run_batch(page, batch, 2, 12_000, 2, 250)
                for row in rows:
                    index = int(row["catalogIndex"])
                    row["retryPass"] = 1
                    by_index[index] = enrich(row, catalog[index])
                print(json.dumps({"retryProcessed":min(start+50,len(unresolved_indices)),"retryTotal":len(unresolved_indices)},ensure_ascii=False),flush=True)
                await asyncio.sleep(0.5)
        await browser.close()

    rows = [by_index[i] for i in range(len(catalog))]
    good = [r for r in rows if is_success(r)]
    unresolved = [r for r in rows if not is_success(r)]
    publisher = [r for r in good if r.get("publisherFields")]
    exact = [r for r in good if r.get("publisherExactUnicorn")]
    state = summary(len(catalog), rows, started)
    state["completionRatio"] = len(good)/len(catalog) if catalog else 0
    save_json(out / "summary.json", state)
    save_json(out / "navigation-log.json", navigations)
    save_json(out / "all-detail-results.json", rows)
    save_json(out / "publisher-detail-results.json", publisher)
    save_json(out / "exact-unicorn-products.json", {
        "criterion":"상품상세 attributes의 필드명이 출판사를 포함하고 값이 공백 정규화 후 정확히 유니콘",
        "count":len(exact),"products":exact,
    })
    save_json(out / "unresolved-products.json", {"count":len(unresolved),"products":unresolved})
    print(json.dumps(state, ensure_ascii=False, indent=2), flush=True)


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
