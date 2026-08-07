#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path
from typing import Any

import requests
from playwright.async_api import async_playwright

BASE = "https://shop.coupang.com"
SELLER_ID = "A00214628"
STORE_ID = 79545
LISTING = f"{BASE}/api/v1/listing"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/150.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.7",
    "Content-Type": "application/json",
    "Origin": BASE,
    "Referer": f"{BASE}/{SELLER_ID}",
}


def save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize(value: Any) -> str:
    return re.sub(r"[\s,·ㆍ/|:：]+", "", str(value or "")).strip()


def extract_fields(node: Any, path: str = "$") -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    publishers: list[dict[str, Any]] = []
    kc: list[dict[str, str]] = []
    if isinstance(node, dict):
        name = str(node.get("name") or node.get("title") or node.get("label") or "")
        value = node.get("value")
        if normalize(name) in {"출판사", "저자출판사"} and value not in (None, ""):
            publishers.append({"path": path, "name": name, "value": str(value), "raw": node})
        for key, child in node.items():
            key_text = str(key)
            if re.search(r"certification|kc|인증", key_text, re.I):
                text = json.dumps(child, ensure_ascii=False) if isinstance(child, (dict, list)) else str(child)
                for number in sorted(set(re.findall(r"\b[A-Z]{1,4}\d{2,4}[A-Z]?\d{2,4}-\d{2,5}\b", text))):
                    kc.append({"path": f"{path}.{key_text}", "number": number})
            p, k = extract_fields(child, f"{path}.{key_text}")
            publishers.extend(p); kc.extend(k)
    elif isinstance(node, list):
        for i, child in enumerate(node):
            p, k = extract_fields(child, f"{path}[{i}]")
            publishers.extend(p); kc.extend(k)
    return publishers, kc


def listing_match(session: requests.Session, row: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    title = str(row.get("catalogSourceName") or "").strip()
    queries = [title, title[:30], str(row.get("catalogProductId") or "")]
    for query in dict.fromkeys(q for q in queries if q):
        payload = {
            "storeId": STORE_ID, "brandId": 0, "vendorId": SELLER_ID,
            "enableAdultItemDisplay": True, "nextPageKey": 0,
            "filter": "SORT_KEY:POPULARITY", "query": query,
        }
        try:
            r = session.post(LISTING, headers=HEADERS, json=payload, timeout=25)
            body = r.json() if r.status_code == 200 else {}
            data = body.get("data") or {}
            for raw in data.get("products") or []:
                if str(raw.get("vendorItemId") or "") == str(row.get("catalogVendorItemId") or ""):
                    return raw, str(data.get("searchId") or "")
        except Exception:
            pass
    return None, ""


async def post(page, body: dict[str, Any]) -> dict[str, Any]:
    return await page.evaluate("""async (body) => {
      const c=new AbortController(); const t=setTimeout(()=>c.abort(),20000);
      try {
        const r=await fetch('/api/v2/store/individualInfo/products',{
          method:'POST',credentials:'include',signal:c.signal,
          headers:{'content-type':'application/json','accept':'application/json, text/plain, */*'},
          body:JSON.stringify(body)});
        const text=await r.text(); let parsed=null; try{parsed=JSON.parse(text)}catch(_){}
        return {status:r.status,length:text.length,parsed,prefix:text.slice(0,500)};
      } catch(e) { return {status:null,length:0,error:`${e?.name||'Error'}: ${e?.message||String(e)}`}; }
      finally { clearTimeout(t); }
    }""", body)


async def main_async() -> None:
    ap=argparse.ArgumentParser()
    ap.add_argument('--input',type=Path,required=True)
    ap.add_argument('--shard',type=int,required=True)
    ap.add_argument('--shards',type=int,required=True)
    ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args()
    source=json.loads(args.input.read_text(encoding='utf-8'))
    products=list(source.get('products') or [])
    selected=[r for i,r in enumerate(products) if i % args.shards == args.shard]
    session=requests.Session(); results=[]
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True,args=['--disable-blink-features=AutomationControlled'])
        ctx=await browser.new_context(locale='ko-KR',timezone_id='Asia/Seoul',user_agent=HEADERS['User-Agent'])
        page=await ctx.new_page()
        try: await page.goto(f'{BASE}/{SELLER_ID}',wait_until='domcontentloaded',timeout=60000)
        except Exception: pass
        for n,row in enumerate(selected,1):
            vi=str(row.get('catalogVendorItemId') or '')
            base={"vendorItemIds":[vi],"isVIBased":True,"storeId":STORE_ID,"vendorId":SELLER_ID,"ignoreAdultCheck":True}
            attempts=[]; best=None
            variants=[('minimal',base)]
            raw,search_id=listing_match(session,row)
            if raw:
                direct={k:raw.get(k) for k in ('sourceFeedId','sourceSearchId','clickEventId','lptag','spec','src','wPcid','pageType','source','productListRules') if raw.get(k) not in (None,'',[],{})}
                if search_id and not direct.get('sourceSearchId'): direct['sourceSearchId']=search_id
                variants.append(('metadata',{**base,**direct}))
            for name,body in variants:
                response=await post(page,body)
                attempts.append({"variant":name,"status":response.get('status'),"length":response.get('length'),"prefix":response.get('prefix'),"error":response.get('error')})
                parsed=response.get('parsed') or {}
                if response.get('status')==200 and int(parsed.get('code') or 0)==200 and parsed.get('data'):
                    best=parsed.get('data'); break
                await page.wait_for_timeout(120)
            publishers,kc=extract_fields(best) if best else ([],[])
            exact=any(normalize(x.get('value'))=='유니콘' for x in publishers)
            result={**row,"recovered":bool(best),"publisherFields":publishers,"publisherExactUnicorn":exact,"kcSignals":kc,"attempts":attempts,"recoveredData":best}
            results.append(result)
            print(json.dumps({"shard":args.shard,"done":n,"total":len(selected),"vendorItemId":vi,"recovered":bool(best),"exact":exact},ensure_ascii=False),flush=True)
        await browser.close()
    save(args.output,{"shard":args.shard,"selected":len(selected),"recovered":sum(r['recovered'] for r in results),"exact":sum(r['publisherExactUnicorn'] for r in results),"results":results})

if __name__=='__main__':
    asyncio.run(main_async())
