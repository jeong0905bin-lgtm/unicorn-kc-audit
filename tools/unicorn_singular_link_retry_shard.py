#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from playwright.async_api import async_playwright

BASE = "https://shop.coupang.com"
SELLER_ID = "A00214628"
STORE_ID = 79545
ENDPOINT = "/api/v2/store/individualInfo/product"


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def normalize_label(value: Any) -> str:
    return re.sub(r"[\s,，·ㆍ:：/\\()\[\]_-]+", "", str(value or ""))


def normalize_value(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def classify(row: dict[str, Any], original: dict[str, Any]) -> dict[str, Any]:
    attributes = row.get("attributes") if isinstance(row.get("attributes"), list) else []
    publisher_fields = [
        {
            "id": item.get("id"),
            "name": str(item.get("name") or ""),
            "value": str(item.get("value") or ""),
            "expose": item.get("expose"),
        }
        for item in attributes
        if isinstance(item, dict) and "출판사" in normalize_label(item.get("name"))
    ]
    exact = [item for item in publisher_fields if normalize_value(item.get("value")) == "유니콘"]
    row.update({
        "catalogIndex": int(original.get("catalogIndex")),
        "catalogProductId": str(original.get("catalogProductId") or original.get("productId") or ""),
        "catalogItemId": str(original.get("catalogItemId") or original.get("itemId") or ""),
        "catalogVendorItemId": str(original.get("catalogVendorItemId") or original.get("requestedVendorItemId") or original.get("vendorItemId") or ""),
        "catalogSourceName": str(original.get("catalogSourceName") or original.get("sourceName") or ""),
        "catalogProductUrl": str(original.get("catalogProductUrl") or original.get("productUrl") or ""),
        "publisherFields": publisher_fields,
        "publisherExactMatches": exact,
        "publisherExactUnicorn": bool(exact),
    })
    return row


def success(row: dict[str, Any]) -> bool:
    return row.get("httpStatus") == 200 and int(row.get("apiCode") or 0) == 200 and bool(row.get("dataPresent"))


def link_extras(link: str) -> dict[str, Any]:
    if not link:
        return {}
    query = parse_qs(urlparse(link).query)
    output: dict[str, Any] = {}
    search_id = (query.get("searchId") or [""])[0]
    source_type = (query.get("sourceType") or [""])[0]
    if search_id:
        output["sourceSearchId"] = search_id
    if source_type:
        output["source"] = source_type
    return output


async def open_page(context, round_number: int, sequence: int):
    page = await context.new_page()
    page.set_default_timeout(60_000)
    url = f"{BASE}/{SELLER_ID}?linkRetryRound={round_number}&sequence={sequence}&cb={random.randint(100000,999999)}"
    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        navigation = {"requested": url, "status": response.status if response else None, "url": page.url, "title": await page.title()}
    except Exception as exc:
        navigation = {"requested": url, "error": f"{type(exc).__name__}: {exc}", "url": page.url}
    return page, navigation


async def fetch_batch(page, items: list[dict[str, Any]], concurrency: int) -> list[dict[str, Any]]:
    return await page.evaluate(
        """async ({items, endpoint, storeId, vendorId, concurrency}) => {
          const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
          const scanSignals = data => {
            const out = []; const seen = new Set();
            const walk = (value, path, depth) => {
              if (value == null || depth > 8 || out.length >= 120) return;
              if (Array.isArray(value)) { value.slice(0,100).forEach((child,index)=>walk(child,`${path}[${index}]`,depth+1)); return; }
              if (typeof value === 'object') {
                for (const [key, child] of Object.entries(value)) {
                  const next = `${path}.${key}`;
                  if (/kc|cert|safety|auth|인증|안전/i.test(key)) {
                    let text; try { text = typeof child === 'string' ? child : JSON.stringify(child); } catch { text = String(child); }
                    const token = `${next}:${text}`;
                    if (!seen.has(token)) { seen.add(token); out.push({path:next,value:text.slice(0,1800)}); }
                  }
                  walk(child,next,depth+1);
                }
                return;
              }
              const text = String(value);
              if (/\b[A-Z]{1,4}\d{2,}[A-Z]?\d*(?:-\d+)+\b/i.test(text)) {
                const token = `${path}:${text}`;
                if (!seen.has(token)) { seen.add(token); out.push({path,value:text.slice(0,1800)}); }
              }
            };
            walk(data,'$',0); return out;
          };
          const variants = item => {
            const extra = item.linkExtras || {};
            const search = extra.sourceSearchId || '';
            const source = extra.source || '';
            const values = [
              ['minimal', {}],
              ['link', {...extra}],
              ['link-store', {...extra, pageType:'STORE'}],
              ['brandstore', {...extra, source:source || 'brandstore', pageType:'BRANDSTORE'}],
              ['brandstore-sdp', {...extra, source:'brandstore_sdp_atf', pageType:'BRANDSTORE_SDP'}],
              ['search-only', search ? {sourceSearchId:search} : {}]
            ];
            const out=[]; const seen=new Set();
            for (const [name,value] of values) {
              const clean={}; for (const [key,child] of Object.entries(value)) if (child != null && child !== '') clean[key]=child;
              const token=JSON.stringify(clean,Object.keys(clean).sort());
              if (!seen.has(token)) { seen.add(token); out.push([name,clean]); }
            }
            return out;
          };
          const requestOne = async item => {
            const attempts=[];
            let chosen=null;
            for (const [variant, extra] of variants(item)) {
              const controller=new AbortController();
              const timer=setTimeout(()=>controller.abort(),20000);
              try {
                const response=await fetch(endpoint,{
                  method:'POST',credentials:'include',signal:controller.signal,
                  headers:{'content-type':'application/json','accept':'application/json, text/plain, */*'},
                  body:JSON.stringify({
                    vendorItemIds:[item.vendorItemId],isVIBased:true,storeId,vendorId,ignoreAdultCheck:true,...extra
                  })
                });
                const text=await response.text();
                let parsed=null; try { parsed=JSON.parse(text); } catch {}
                const data=parsed && parsed.data && typeof parsed.data === 'object' ? parsed.data : null;
                const attempt={variant,status:response.status,length:text.length,code:parsed?parsed.code:null,message:parsed?(parsed.msg||parsed.message||null):null,dataPresent:Boolean(data),prefix:text.slice(0,650)};
                attempts.push(attempt);
                if (response.status===200 && parsed && Number(parsed.code)===200 && data) {
                  const image=data.imageAndTitleArea||{};
                  chosen={
                    catalogIndex:item.catalogIndex,requestedVendorItemId:item.vendorItemId,
                    httpStatus:response.status,responseLength:text.length,responsePrefix:text.slice(0,650),
                    apiCode:parsed.code,apiMessage:parsed.msg||parsed.message||null,dataPresent:true,
                    responseVariant:variant,
                    productId:String(data.productId||item.productId||''),itemId:String(data.itemId||item.itemId||''),
                    vendorItemId:String(data.vendorItemId||item.vendorItemId||''),sourceName:String(image.title||''),
                    groupTitle:String(image.groupTitle||''),mainImageUrl:String(image.completeHttpUrl||image.defaultUrl||''),
                    detailImageUrls:image.completeHttpDetailImageUrls||image.detailImageUrls||[],
                    attributes:Array.isArray(data.attributes)?data.attributes:[],valid:data.valid,adult:data.adult,
                    productLink:String(data.link||''),kcSignals:scanSignals(data),attempts
                  };
                  break;
                }
              } catch (error) {
                attempts.push({variant,status:null,dataPresent:false,error:`${error?.name||'Error'}: ${error?.message||String(error)}`});
              } finally { clearTimeout(timer); }
              await sleep(160+Math.floor(Math.random()*220));
            }
            if (chosen) return chosen;
            const last=attempts[attempts.length-1]||{};
            return {
              catalogIndex:item.catalogIndex,requestedVendorItemId:item.vendorItemId,
              httpStatus:last.status??null,responseLength:last.length,apiCode:last.code,apiMessage:last.message,
              responsePrefix:last.prefix,dataPresent:false,error:last.error,responseVariant:last.variant,attempts
            };
          };
          const results=new Array(items.length); let cursor=0;
          const worker=async()=>{ while(true){ const index=cursor++; if(index>=items.length)return; results[index]=await requestOne(items[index]); await sleep(180+Math.floor(Math.random()*260)); } };
          await Promise.all(Array.from({length:Math.max(1,concurrency)},worker));
          return results;
        }""",
        {"items": items, "endpoint": ENDPOINT, "storeId": STORE_ID, "vendorId": SELLER_ID, "concurrency": max(1, concurrency)},
    )


async def main_async() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--unresolved", type=Path, required=True)
    parser.add_argument("--plural", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--shard-index", type=int, required=True)
    parser.add_argument("--shard-count", type=int, required=True)
    parser.add_argument("--rounds", type=int, default=3)
    args = parser.parse_args()

    unresolved_doc = json.loads(args.unresolved.read_text(encoding="utf-8"))
    plural_rows = json.loads(args.plural.read_text(encoding="utf-8"))
    plural_map = {int(row["catalogIndex"]): row for row in plural_rows if row.get("catalogIndex") is not None}
    all_products = list(unresolved_doc.get("products") or [])
    products = [row for row in all_products if int(row["catalogIndex"]) % args.shard_count == args.shard_index]
    originals = {int(row["catalogIndex"]): row for row in products}
    pending = sorted(originals)
    recovered: dict[int, dict[str, Any]] = {}
    latest: dict[int, dict[str, Any]] = {}
    attempts: list[dict[str, Any]] = []
    navigations: list[dict[str, Any]] = []
    started = time.time()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        context = await browser.new_context(
            locale="ko-KR", timezone_id="Asia/Seoul", viewport={"width":1440,"height":1000},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
        )
        sequence=0
        for round_number in range(1,args.rounds+1):
            if not pending: break
            round_indices=list(pending); pending=[]
            for start in range(0,len(round_indices),12):
                sequence+=1
                page,navigation=await open_page(context,round_number,sequence)
                navigation.update({"round":round_number,"sequence":sequence}); navigations.append(navigation)
                indices=round_indices[start:start+12]
                batch=[]
                for index in indices:
                    original=originals[index]
                    plural=plural_map.get(index,{})
                    batch.append({
                        "catalogIndex":index,
                        "productId":str(original.get("catalogProductId") or original.get("productId") or ""),
                        "itemId":str(original.get("catalogItemId") or original.get("itemId") or ""),
                        "vendorItemId":str(original.get("catalogVendorItemId") or original.get("requestedVendorItemId") or original.get("vendorItemId") or ""),
                        "linkExtras":link_extras(str(plural.get("productLink") or "")),
                    })
                try:
                    rows=await fetch_batch(page,batch,2 if round_number==1 else 1)
                except Exception as exc:
                    rows=[{"catalogIndex":item["catalogIndex"],"requestedVendorItemId":item["vendorItemId"],"httpStatus":None,"dataPresent":False,"error":f"batch failure: {type(exc).__name__}: {exc}","attempts":[]} for item in batch]
                await page.close()
                for row in rows:
                    index=int(row["catalogIndex"]); row["retryRound"]=round_number; row["shardIndex"]=args.shard_index
                    row=classify(row,originals[index]); latest[index]=row
                    for attempt in row.get("attempts") or []:
                        attempts.append({"catalogIndex":index,"retryRound":round_number,"variant":attempt.get("variant"),"httpStatus":attempt.get("status"),"apiCode":attempt.get("code"),"responseLength":attempt.get("length"),"dataPresent":attempt.get("dataPresent"),"error":attempt.get("error")})
                    row.pop("attempts",None)
                    if success(row): recovered[index]=row
                    else: pending.append(index)
                await asyncio.sleep(1+random.random()*1.5)
            pending=sorted(set(pending)-set(recovered))
            print(json.dumps({"shard":args.shard_index,"round":round_number,"input":len(round_indices),"recoveredTotal":len(recovered),"remaining":len(pending),"exactUnicorn":sum(bool(row.get("publisherExactUnicorn")) for row in recovered.values()),"elapsedSeconds":round(time.time()-started,2)},ensure_ascii=False),flush=True)
            if pending: await asyncio.sleep(5+round_number*3+random.random()*3)
        await browser.close()

    recovered_rows=[recovered[index] for index in sorted(recovered)]
    unresolved_rows=[latest.get(index) or originals[index] for index in pending]
    exact_rows=[row for row in recovered_rows if row.get("publisherExactUnicorn")]
    result={
        "sourceUnresolvedCount":len(all_products),"shardIndex":args.shard_index,"shardCount":args.shard_count,
        "inputCount":len(products),"recoveredCount":len(recovered_rows),"unresolvedCount":len(unresolved_rows),
        "newExactUnicornCount":len(exact_rows),"elapsedSeconds":round(time.time()-started,2),
        "recovered":recovered_rows,"exactUnicorn":exact_rows,"unresolved":unresolved_rows,
        "attempts":attempts,"navigations":navigations,
    }
    save_json(args.output,result)
    print(json.dumps({key:result[key] for key in ("shardIndex","inputCount","recoveredCount","unresolvedCount","newExactUnicornCount","elapsedSeconds")},ensure_ascii=False,indent=2))


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
