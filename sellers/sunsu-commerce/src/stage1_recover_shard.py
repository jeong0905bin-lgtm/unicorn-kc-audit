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
BASE_QUERIES=['','퍼즐','판퍼즐','대판퍼즐','직소퍼즐','조각','색칠','색칠북','그림책','놀이북','스티커','스티커북','컬렉션북','워터','두들북','만들기','공부','한글','숫자','영어','알파벳','미로','색종이','종이접기','카드','보드게임','자석','가방','세트','뽀로로','타요','핑크퐁','아기상어','캐치티니핑','티니핑','산리오','헬로키티','쿠로미','마이멜로디','시나모롤','포켓몬','피카츄','디즈니','겨울왕국','엘사','미키','미니','프린세스','공룡','로봇','자동차','동물','바다','곤충','한글용사','브레드이발소','신비아파트','콩순이','또봇','카봇','옥토넛','폴리','슈퍼윙스','미니특공대','시크릿쥬쥬','소피루비','레인보우루비','라바','짱구','도라에몽','귀멸','원피스','마블','스파이더맨','어몽어스','LOL','유니콘','BOOKFRIENDS','공구','낚시','베이커리','마녀','메탈카드봇','다이노스터','경찰','마그네틱','스탬프','스크래치','네일','퀸','킹','토이북','플랩북','사운드북','오리기','붙이기','그리기','꾸미기','물감','크레용','색연필','도장','블록','큐브','게임','교육','학습','유아','어린이','선물']
# Every Hangul onset/vowel combination without a final consonant. This avoids guessing
# missing titles while keeping the request count bounded: 399 queries / 8 shards.
HANGUL_OPEN=[chr(0xAC00 + onset*21*28 + vowel*28) for onset in range(19) for vowel in range(21)]
ASCII_QUERIES=list('0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz')
QUERIES=list(dict.fromkeys(BASE_QUERIES + HANGUL_OPEN + ASCII_QUERIES))
SORTS=['POPULARITY','LATEST','LOW_PRICE','HIGH_PRICE','SALE']

def now(): return datetime.now(timezone.utc).isoformat()
def text(v:Any)->str: return '' if v is None else str(v)
def normalize(raw:dict[str,Any], source:str):
    pid=raw.get('productId') or raw.get('catalogProductId') or raw.get('productID')
    if not pid:return None
    item=raw.get('itemId') or raw.get('catalogItemId') or ''
    vendor=raw.get('vendorItemId') or raw.get('catalogVendorItemId') or ''
    area=raw.get('imageAndTitleArea') if isinstance(raw.get('imageAndTitleArea'),dict) else {}
    name=raw.get('productName') or raw.get('catalogSourceName') or raw.get('name') or raw.get('title') or area.get('title') or area.get('groupTitle') or ''
    link=raw.get('link') or ''
    url='https://www.coupang.com'+link if link.startswith('/vp/products/') else f'https://www.coupang.com/vp/products/{pid}'
    if '?' not in url and (item or vendor):url+=f'?itemId={item}&vendorItemId={vendor}'
    return {'productId':text(pid),'itemId':text(item),'vendorItemId':text(vendor),'productName':text(name).strip(),'productUrl':url,'sellerId':SELLER_ID,'storeId':STORE_ID,'verificationStatus':'seller-listing-api','rawListing':raw,'sources':[source]}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--shard',type=int,required=True); ap.add_argument('--shards',type=int,default=8); ap.add_argument('--out',type=Path,required=True); a=ap.parse_args()
    session=requests.Session(); store={}; events=[]
    selected=[q for i,q in enumerate(QUERIES) if i%a.shards==a.shard]
    for query in selected:
        for sort in SORTS:
            payload={'storeId':STORE_ID,'brandId':0,'vendorId':SELLER_ID,'enableAdultItemDisplay':True,'nextPageKey':0,'filter':f'SORT_KEY:{sort}','query':query}
            try:
                r=session.post(LISTING,headers=HEADERS,json=payload,timeout=12)
                body=r.json() if r.status_code==200 else {}
                data=body.get('data') or {}; products=data.get('products') or []; added=0
                for raw in products:
                    row=normalize(raw,f'listing:q:{query or "ALL"}:sort:{sort}')
                    if not row:continue
                    pid=row['productId']
                    if pid not in store:store[pid]=row; added+=1
                    else:
                        store[pid]['sources']=sorted(set(store[pid]['sources']+row['sources']))
                        if not store[pid].get('productName') and row.get('productName'):store[pid]['productName']=row['productName']
                events.append({'query':query,'sort':sort,'status':r.status_code,'count':len(products),'added':added,'total':len(store)})
                if added: print({'shard':a.shard,'query':query or 'ALL','sort':sort,'count':len(products),'added':added,'total':len(store)},flush=True)
            except Exception as e: events.append({'query':query,'sort':sort,'error':f'{type(e).__name__}: {e}'})
            time.sleep(.02)
    rows=sorted(store.values(),key=lambda x:int(x['productId']))
    out={'seller':SELLER,'sellerId':SELLER_ID,'storeId':STORE_ID,'shard':a.shard,'shards':a.shards,'queryCount':len(selected),'count':len(rows),'events':events,'products':rows,'generatedAt':now()}
    a.out.parent.mkdir(parents=True,exist_ok=True); a.out.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print({'shard':a.shard,'finalCount':len(rows)},flush=True)
if __name__=='__main__':main()
