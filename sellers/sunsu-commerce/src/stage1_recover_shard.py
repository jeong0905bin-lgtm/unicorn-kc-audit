#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import requests

SELLER='순수커머스'
SELLER_ID='A01593407'
STORE_ID=297717
BASE='https://shop.coupang.com'
LISTING=f'{BASE}/api/v1/listing'
HEADERS={
 'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/150.0.0.0 Safari/537.36',
 'Accept':'application/json, text/plain, */*','Accept-Language':'ko-KR,ko;q=0.9,en-US;q=0.7',
 'Content-Type':'application/json','Origin':BASE,'Referer':f'{BASE}/{SELLER_ID}'
}
PAGE_SIZE=20

def now(): return datetime.now(timezone.utc).isoformat()
def text(v:Any)->str: return '' if v is None else str(v)

def first_text(node:Any, keys:tuple[str,...])->str:
    if isinstance(node,dict):
        for k in keys:
            v=node.get(k)
            if isinstance(v,(str,int,float)) and str(v).strip(): return str(v).strip()
        for v in node.values():
            got=first_text(v,keys)
            if got:return got
    elif isinstance(node,list):
        for v in node:
            got=first_text(v,keys)
            if got:return got
    return ''

def normalize(raw:dict[str,Any], source:str)->dict[str,Any]|None:
    pid=raw.get('productId') or raw.get('catalogProductId') or raw.get('productID')
    if not pid:return None
    item=raw.get('itemId') or raw.get('catalogItemId') or ''
    vendor=raw.get('vendorItemId') or raw.get('catalogVendorItemId') or ''
    name=(raw.get('productName') or raw.get('catalogSourceName') or raw.get('name') or raw.get('title')
          or first_text(raw.get('imageAndTitleArea') or {},('title','name','productName','text')))
    url=f'https://www.coupang.com/vp/products/{pid}'
    if item or vendor:url+=f'?itemId={item}&vendorItemId={vendor}'
    return {'productId':text(pid),'itemId':text(item),'vendorItemId':text(vendor),'productName':text(name).strip(),'productUrl':url,'sellerId':SELLER_ID,'storeId':STORE_ID,'verificationStatus':'seller-listing-api','rawListing':raw,'sources':[source]}

def extract_products(body:Any)->list[dict[str,Any]]:
    if not isinstance(body,dict):return []
    data=body.get('data')
    if isinstance(data,dict) and isinstance(data.get('products'),list):return data['products']
    if isinstance(body.get('products'),list):return body['products']
    return []

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--shard',type=int,required=True);ap.add_argument('--shards',type=int,default=8);ap.add_argument('--out',type=Path,required=True);a=ap.parse_args()
    session=requests.Session(); store={}; events=[]
    # The API returns 20 products but no nextPageKey. Treat nextPageKey as a deterministic offset.
    # Each shard owns offsets shard*20, (shard+shards)*20, ... so pages never overlap.
    consecutive_empty=0
    for page_index in range(a.shard,80,a.shards):
        offset=page_index*PAGE_SIZE
        payload={'storeId':STORE_ID,'brandId':0,'vendorId':SELLER_ID,'enableAdultItemDisplay':True,
                 'nextPageKey':offset,'filter':'SORT_KEY:POPULARITY','query':''}
        try:
            r=session.post(LISTING,headers=HEADERS,json=payload,timeout=20)
            try: body=r.json()
            except Exception: body={}
            products=extract_products(body)
            added=0
            for raw in products:
                row=normalize(raw,f'listing:POPULARITY:offset:{offset}')
                if not row:continue
                pid=row['productId']
                if pid not in store:store[pid]=row;added+=1
                else:store[pid]['sources']=sorted(set(store[pid]['sources']+row['sources']))
            events.append({'pageIndex':page_index,'offset':offset,'status':r.status_code,'count':len(products),'added':added,
                           'topKeys':sorted(body.keys()) if isinstance(body,dict) else []})
            print({'shard':a.shard,'pageIndex':page_index,'offset':offset,'count':len(products),'added':added,'total':len(store)},flush=True)
            if not products:
                consecutive_empty+=1
                if consecutive_empty>=2:break
            else:
                consecutive_empty=0
            # A partial page is the end of the seller catalog for this ordering.
            if 0 < len(products) < PAGE_SIZE:break
            time.sleep(.12)
        except Exception as e:
            events.append({'pageIndex':page_index,'offset':offset,'error':f'{type(e).__name__}: {e}'})
            consecutive_empty+=1
            if consecutive_empty>=2:break
    rows=sorted(store.values(),key=lambda x:int(x['productId']))
    out={'seller':SELLER,'sellerId':SELLER_ID,'storeId':STORE_ID,'shard':a.shard,'shards':a.shards,'sort':'POPULARITY','count':len(rows),'events':events,'products':rows,'generatedAt':now()}
    a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print({'shard':a.shard,'finalCount':len(rows)},flush=True)
if __name__=='__main__':main()
