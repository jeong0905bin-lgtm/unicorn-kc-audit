#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, re
from datetime import datetime, timezone
from pathlib import Path

SELLER='순수커머스'; SELLER_ID='A01593407'; STORE_ID=297717; EXPECTED=195
UNICORN_RE=re.compile(r'(?:\(주\)\s*유니콘|주식회사\s*유니콘|BOOKFRIENDS|UNICORN|(?<![가-힣])유니콘(?![가-힣]))',re.I)

def now(): return datetime.now(timezone.utc).isoformat()
def listing_key(row): return '|'.join(str(row.get(k) or '') for k in ('productId','itemId','vendorItemId'))
def merge_row(dst,src):
    for k in ['itemId','vendorItemId','productName','productUrl','rawListing']:
        if not dst.get(k) and src.get(k): dst[k]=src[k]
    dst['sources']=sorted(set(dst.get('sources',[])+src.get('sources',[])))

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--input-dir',type=Path,required=True); ap.add_argument('--out',type=Path,required=True); ap.add_argument('--candidates-out',type=Path,required=True); ap.add_argument('--summary-out',type=Path,required=True); a=ap.parse_args()
    listings={}; products={}; shard_counts={}; invalid=[]
    for f in sorted(a.input_dir.rglob('shard-*.json')):
        try: data=json.loads(f.read_text(encoding='utf-8'))
        except Exception as e: invalid.append({'file':str(f),'error':str(e)}); continue
        shard_counts[f.name]=data.get('count',0)
        if str(data.get('sellerId'))!=SELLER_ID or int(data.get('storeId') or 0)!=STORE_ID:
            invalid.append({'file':str(f),'error':'seller/store mismatch'}); continue
        for row in data.get('products',[]):
            pid=str(row.get('productId') or '').strip()
            if not pid: continue
            row['verificationStatus']='seller-listing-api'
            lk=listing_key(row)
            if lk not in listings: listings[lk]=row
            else: merge_row(listings[lk],row)
            if pid not in products: products[pid]=dict(row)
            else: merge_row(products[pid],row)
    listing_rows=sorted(listings.values(),key=lambda x:(int(x['productId']),str(x.get('itemId','')),str(x.get('vendorItemId',''))))
    verified=sorted(products.values(),key=lambda x:int(x['productId']))
    candidates=[]
    for row in verified:
        raw=json.dumps(row.get('rawListing') or {},ensure_ascii=False); text=' '.join([row.get('productName',''),raw])
        if UNICORN_RE.search(text): row['publisherGrade']='확정'; row['publisherReason']='판매자 목록 API 상품명/메타데이터 직접 표기'; candidates.append(row)
        elif any(k in row.get('productName','') for k in ['퍼즐','스티커','색칠북','컬렉션북','워터','놀이북']): row['publisherGrade']='후보'; row['publisherReason']='ISBN·공식 도서정보 교차검증 필요'; candidates.append(row)
        else: row['publisherGrade']='미확정'; row['publisherReason']='직접 근거 없음'
    summary={'discoveredSellerListings':len(listing_rows),'discoveredUniqueProductIds':len(verified),'sellerConfirmed':len(verified),'remainingUnresolved':max(0,EXPECTED-len(listing_rows)),'expectedListingCount':EXPECTED,'catalogComplete':len(listing_rows)>=EXPECTED,'publisherCandidates':len(candidates),'invalidShardFiles':len(invalid)}
    payload={'seller':{'name':SELLER,'sellerId':SELLER_ID,'storeId':STORE_ID,'expectedListingCount':EXPECTED},'summary':summary,'shardCounts':shard_counts,'listings':listing_rows,'products':verified,'unresolvedProducts':[],'invalidInputs':invalid,'generatedAt':now()}
    a.out.parent.mkdir(parents=True,exist_ok=True); a.out.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8'); a.candidates_out.write_text(json.dumps(candidates,ensure_ascii=False,indent=2),encoding='utf-8'); a.summary_out.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(summary,flush=True)
    if len(listing_rows)<EXPECTED: raise SystemExit(f'stage1 incomplete: expected {EXPECTED} seller listings, found {len(listing_rows)}; unique productIds {len(verified)}')
if __name__=='__main__': main()
