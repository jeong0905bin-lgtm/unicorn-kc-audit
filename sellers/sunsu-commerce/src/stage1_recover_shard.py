#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import requests

SELLER='순수커머스'; SELLER_ID='A01593407'; STORE_ID=297717
BASE='https://shop.coupang.com'; LISTING=f'{BASE}/api/v1/listing'
HEADERS={'User-Agent':'Mozilla/5.0','Accept':'application/json, text/plain, */*','Accept-Language':'ko-KR,ko;q=0.9','Content-Type':'application/json','Origin':BASE,'Referer':f'{BASE}/{SELLER_ID}'}
BASE_QUERIES=['','퍼즐','판퍼즐','색칠','색칠북','스티커','스티커북','놀이북','그림책','세트','캐치티니핑','티니핑','산리오','포켓몬','디즈니','공룡','유니콘','BOOKFRIENDS','어린이','유아','선물','만들기','게임']
HANGUL=[chr(0xAC00 + o*21*28 + v*28) for o in range(19) for v in range(21)]
QUERIES=list(dict.fromkeys(BASE_QUERIES+HANGUL+list('0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz')))
SORTS=['POPULARITY','LATEST','LOW_PRICE','HIGH_PRICE','SALE']

def now(): return datetime.now(timezone.utc).isoformat()
def s(v:Any)->str: return '' if v is None else str(v)
def norm(raw:dict[str,Any],source:str):
    pid=raw.get('productId') or raw.get('catalogProductId') or raw.get('productID')
    if not pid:return None
    iid=raw.get('itemId') or raw.get('catalogItemId') or ''
    vid=raw.get('vendorItemId') or raw.get('catalogVendorItemId') or ''
    area=raw.get('imageAndTitleArea') if isinstance(raw.get('imageAndTitleArea'),dict) else {}
    name=raw.get('productName') or raw.get('catalogSourceName') or raw.get('name') or raw.get('title') or area.get('title') or area.get('groupTitle') or ''
    link=raw.get('link') or ''
    url='https://www.coupang.com'+link if str(link).startswith('/vp/products/') else f'https://www.coupang.com/vp/products/{pid}'
    if '?' not in url and (iid or vid): url+=f'?itemId={iid}&vendorItemId={vid}'
    return {'productId':s(pid),'itemId':s(iid),'vendorItemId':s(vid),'productName':s(name).strip(),'productUrl':url,'sellerId':SELLER_ID,'storeId':STORE_ID,'verificationStatus':'seller-listing-api','rawListing':raw,'sources':[source]}

def key(row:dict[str,Any])->str:
    return '|'.join([row.get('productId',''),row.get('itemId',''),row.get('vendorItemId','')])

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--shard',type=int,required=True); ap.add_argument('--shards',type=int,default=8); ap.add_argument('--out',type=Path,required=True); a=ap.parse_args()
    sess=requests.Session(); store={}; events=[]
    for query in [q for i,q in enumerate(QUERIES) if i%a.shards==a.shard]:
        for sort in SORTS:
            payload={'storeId':STORE_ID,'brandId':0,'vendorId':SELLER_ID,'enableAdultItemDisplay':True,'nextPageKey':0,'filter':f'SORT_KEY:{sort}','query':query}
            try:
                r=sess.post(LISTING,headers=HEADERS,json=payload,timeout=12)
                body=r.json() if r.status_code==200 else {}; products=(body.get('data') or {}).get('products') or []; added=0
                for raw in products:
                    row=norm(raw,f'q:{query or "ALL"}:sort:{sort}')
                    if not row: continue
                    k=key(row)
                    if k not in store: store[k]=row; added+=1
                    else: store[k]['sources']=sorted(set(store[k].get('sources',[])+row['sources']))
                events.append({'query':query,'sort':sort,'status':r.status_code,'count':len(products),'added':added,'totalListings':len(store)})
                if added: print({'shard':a.shard,'query':query or 'ALL','sort':sort,'added':added,'totalListings':len(store)},flush=True)
            except Exception as e: events.append({'query':query,'sort':sort,'error':f'{type(e).__name__}: {e}'})
            time.sleep(.01)
    rows=sorted(store.values(),key=lambda x:(int(x['productId']),x.get('itemId',''),x.get('vendorItemId','')))
    out={'seller':SELLER,'sellerId':SELLER_ID,'storeId':STORE_ID,'shard':a.shard,'shards':a.shards,'count':len(rows),'uniqueProductIds':len({r['productId'] for r in rows}),'events':events,'products':rows,'generatedAt':now()}
    a.out.parent.mkdir(parents=True,exist_ok=True); a.out.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print({'shard':a.shard,'finalListings':len(rows),'uniqueProductIds':out['uniqueProductIds']},flush=True)
if __name__=='__main__': main()
