#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, re
from datetime import datetime, timezone
from pathlib import Path
import requests

SELLER='순수커머스'; EXPECTED=195
UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/127 Safari/537.36'
UNICORN_RE=re.compile(r'(?:\(주\)\s*유니콘|주식회사\s*유니콘|BOOKFRIENDS|UNICORN|(?<![가-힣])유니콘(?![가-힣]))',re.I)

def now(): return datetime.now(timezone.utc).isoformat()
def merge_row(dst,src):
    for k in ['itemId','vendorItemId','productName','productUrl']:
        if not dst.get(k) and src.get(k):dst[k]=src[k]
    dst['sources']=sorted(set(dst.get('sources',[])+src.get('sources',[])))

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--input-dir',type=Path,required=True); ap.add_argument('--out',type=Path,required=True); ap.add_argument('--candidates-out',type=Path,required=True); ap.add_argument('--summary-out',type=Path,required=True); a=ap.parse_args()
    store={}; shard_counts={}
    for f in sorted(a.input_dir.rglob('shard-*.json')):
        try:data=json.loads(f.read_text(encoding='utf-8'))
        except Exception:continue
        shard_counts[f.name]=data.get('count',0)
        for row in data.get('products',[]):
            pid=str(row.get('productId','')).strip()
            if not pid:continue
            if pid not in store:store[pid]=row
            else:merge_row(store[pid],row)
    session=requests.Session(); verified=[]; rejected=[]
    for i,row in enumerate(store.values()):
        url=row.get('productUrl') or f"https://www.coupang.com/vp/products/{row['productId']}"
        body=''; status='unverified'
        for host_url in [url,url.replace('www.coupang.com','m.coupang.com')]:
            try:
                r=session.get(host_url,timeout=20,headers={'User-Agent':UA,'Accept-Language':'ko-KR,ko;q=0.9'})
                body+='\n'+r.text
            except Exception:pass
        direct=SELLER in body or 'A01593407' in body
        # Search-index sources can be retained as unresolved, but never marked seller-confirmed.
        if direct:
            status='seller-confirmed'; verified.append(row)
        else:
            row['verificationStatus']='unresolved-seller'; rejected.append(row)
        row['verificationStatus']=status
        text=' '.join([row.get('productName',''),body[:150000]])
        if UNICORN_RE.search(text):
            row['publisherGrade']='확정'; row['publisherReason']='쿠팡 상품명/상세/필수표기 직접 표기'
        elif any(k in row.get('productName','') for k in ['퍼즐','스티커','색칠북','컬렉션북']):
            row['publisherGrade']='후보'; row['publisherReason']='ISBN·공식 도서정보 교차검증 필요'
        else:
            row['publisherGrade']='미확정'; row['publisherReason']='직접 근거 없음'
        if i%25==0:print({'checked':i,'confirmed':len(verified),'discovered':len(store)},flush=True)
    # Preserve all discoveries in diagnostics, while products contains only seller-confirmed records.
    verified=sorted(verified,key=lambda x:int(x['productId']))
    candidates=[x for x in verified if x.get('publisherGrade') in ('확정','후보')]
    payload={'seller':{'name':SELLER,'expectedCount':EXPECTED},'summary':{'discoveredUniqueProductIds':len(store),'sellerConfirmed':len(verified),'remainingUnresolved':len(rejected),'expectedCount':EXPECTED,'catalogComplete':len(verified)==EXPECTED,'publisherCandidates':len(candidates)},'shardCounts':shard_counts,'products':verified,'unresolvedProducts':rejected,'generatedAt':now()}
    a.out.parent.mkdir(parents=True,exist_ok=True)
    a.out.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    a.candidates_out.write_text(json.dumps(candidates,ensure_ascii=False,indent=2),encoding='utf-8')
    a.summary_out.write_text(json.dumps(payload['summary'],ensure_ascii=False,indent=2),encoding='utf-8')
    print(payload['summary'],flush=True)
    if len(verified)!=EXPECTED:raise SystemExit(f"stage1 incomplete: expected {EXPECTED}, confirmed {len(verified)}, discovered {len(store)}, unresolved {len(rejected)}")
if __name__=='__main__':main()
