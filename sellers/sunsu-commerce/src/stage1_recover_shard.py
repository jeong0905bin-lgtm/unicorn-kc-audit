#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import requests

SELLER='순수커머스'; SELLER_ID='A01593407'; STORE_ID=297717
BASE='https://shop.coupang.com'; LISTING=f'{BASE}/api/v1/listing'
HEADERS={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/150.0.0.0 Safari/537.36','Accept':'application/json, text/plain, */*','Accept-Language':'ko-KR,ko;q=0.9,en-US;q=0.7','Content-Type':'application/json','Origin':BASE,'Referer':f'{BASE}/{SELLER_ID}'}
# Empty query returns only one page. Recover the full catalog by partitioning broad title queries.
QUERIES=['','퍼즐','색칠','스티커','북','책','놀이','캐릭터','세트','판','색칠북','스티커북','컬러링','물감','워터','대판','미니','가방','만들기','공부','한글','숫자','영어','공룡','동물','자동차','공주','티니핑','뽀로로','산리오','타요','핑크퐁','아기상어','포켓몬','디즈니','마블','시크릿쥬쥬','캐치티니핑','헬로카봇','브레드이발소','신비아파트','옥토넛','콩순이','또봇','로보카폴리','미니특공대','1','2','3','4','5','6','7','8','9','A','B']
SORTS=['POPULARITY','LATEST','LOW_PRICE','HIGH_PRICE','SALE']

def now(): return datetime.now(timezone.utc).isoformat()
def text(v:Any)->str: return '' if v is None else str(v)
def normalize(raw:dict[str,Any], source:str):
    pid=raw.get('productId') or raw.get('catalogProductId') or raw.get('productID')
    if not pid:return None
    item=raw.get('itemId') or raw.get('catalogItemId') or ''
    vendor=raw.get('vendorItemId') or raw.get('catalogVendorItemId') or ''
    name=raw.get('productName') or raw.get('catalogSourceName') or raw.get('name') or raw.get('title') or ''
    url=f'https://www.coupang.com/vp/products/{pid}'
    if item or vendor:url+=f'?itemId={item}&vendorItemId={vendor}'
    return {'productId':text(pid),'itemId':text(item),'vendorItemId':text(vendor),'productName':text(name).strip(),'productUrl':url,'sellerId':SELLER_ID,'storeId':STORE_ID,'verificationStatus':'seller-listing-api','rawListing':raw,'sources':[source]}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--shard',type=int,required=True); ap.add_argument('--shards',type=int,default=8); ap.add_argument('--out',type=Path,required=True); a=ap.parse_args()
    session=requests.Session(); store={}; events=[]
    selected=[q for i,q in enumerate(QUERIES) if i%a.shards==a.shard]
    for query in selected:
        for sort in SORTS:
            payload={'storeId':STORE_ID,'brandId':0,'vendorId':SELLER_ID,'enableAdultItemDisplay':True,'nextPageKey':0,'filter':f'SORT_KEY:{sort}','query':query}
            try:
                r=session.post(LISTING,headers=HEADERS,json=payload,timeout=20)
                body=r.json() if r.status_code==200 else {}
                data=body.get('data') or {}; products=data.get('products') or []; added=0
                for raw in products:
                    row=normalize(raw,f'listing:q:{query}:sort:{sort}')
                    if not row:continue
                    pid=row['productId']
                    if pid not in store:store[pid]=row; added+=1
                    else:store[pid]['sources']=sorted(set(store[pid]['sources']+row['sources']))
                events.append({'query':query,'sort':sort,'status':r.status_code,'count':len(products),'added':added})
                print({'shard':a.shard,'query':query,'sort':sort,'count':len(products),'added':added,'total':len(store)},flush=True)
            except Exception as e:
                events.append({'query':query,'sort':sort,'error':f'{type(e).__name__}: {e}'})
            time.sleep(.08)
    rows=sorted(store.values(),key=lambda x:int(x['productId']))
    out={'seller':SELLER,'sellerId':SELLER_ID,'storeId':STORE_ID,'shard':a.shard,'shards':a.shards,'queries':selected,'count':len(rows),'events':events,'products':rows,'generatedAt':now()}
    a.out.parent.mkdir(parents=True,exist_ok=True); a.out.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print({'shard':a.shard,'finalCount':len(rows)},flush=True)
if __name__=='__main__':main()
