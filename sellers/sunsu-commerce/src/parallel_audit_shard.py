#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,re
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import quote_plus
import requests
from bs4 import BeautifulSoup

UNICORN_RE=re.compile(r'(?:\(주\)\s*유니콘|주식회사\s*유니콘|BOOKFRIENDS|UNICORN|(?<![가-힣])유니콘(?![가-힣]))',re.I)
KC_RE=re.compile(r'\b(?:CB|CA|SU|U)\d{3,}[A-Z0-9-]{5,}\b',re.I)
EXCLUDED={'U003E1577-7011'}
HEADERS={'User-Agent':'Mozilla/5.0','Accept-Language':'ko-KR,ko;q=0.9'}
def now():return datetime.now(timezone.utc).isoformat()
def fetch(url):
    try:
        r=requests.get(url,headers=HEADERS,timeout=18)
        return BeautifulSoup(r.text,'html.parser').get_text(' ',strip=True),('ok' if r.ok else f'http_{r.status_code}')
    except Exception as e:return '',f'{type(e).__name__}'
def audit(row):
    logs=[]; direct=' '.join(str(row.get(k,'')) for k in ('productName','publisherManufacturer','brand','kcText','rawListing'))
    unicorn=bool(UNICORN_RE.search(direct)); reason='쿠팡 상품명/메타데이터 직접 표기' if unicorn else ''
    name=(row.get('productName') or '').strip()
    if not unicorn and name:
        for base in ('https://search.kyobobook.co.kr/search?keyword=','https://www.yes24.com/Product/Search?domain=ALL&query=','https://www.aladin.co.kr/search/wsearchresult.aspx?SearchTarget=All&SearchWord='):
            url=base+quote_plus(name); page,state=fetch(url); hit=bool(UNICORN_RE.search(page)); logs.append({'productId':row.get('productId'),'stage':'publisher','url':url,'state':state,'hit':hit,'checkedAt':now()})
            if hit: unicorn=True;reason='공식 도서검색 유니콘 표기';break
    row['isUnicorn']=unicorn;row['publisherGrade']='확정' if UNICORN_RE.search(direct) else ('고신뢰 추론' if unicorn else '제외');row['publisherReason']=reason
    if unicorn:
        kc='';url=row.get('productUrl') or f"https://www.coupang.com/vp/products/{row.get('productId')}";page,state=fetch(url)
        for c in KC_RE.findall(page):
            c=c.upper()
            if c not in EXCLUDED:kc=c;break
        row['kcNumber']=kc;row['kcSource']='쿠팡 상세페이지 exact' if kc else 'exact 근거 없음';row['kcStatus']='미검증' if kc else ('검증 불가' if state!='ok' else 'KC 공개표기 없음')
        logs.append({'productId':row.get('productId'),'stage':'kc','url':url,'state':state,'found':kc,'checkedAt':now()})
        if kc:
            surl=f'https://www.safetykorea.kr/release/certificationsearch?certNum={quote_plus(kc)}';spage,sstate=fetch(surl);exact=kc in spage.upper();expired=bool(re.search(r'기간\s*만료|만료|취소|효력\s*상실',spage)) if exact else False
            row['kcStatus']='KC 기간만료' if expired else ('KC 적합' if exact else '검증 불가');row['expired']=expired
            logs.append({'productId':row.get('productId'),'stage':'safetykorea','url':surl,'state':sstate,'exact':exact,'expired':expired,'checkedAt':now()})
    return row,logs

def main():
    p=argparse.ArgumentParser();p.add_argument('--input',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--workers',type=int,default=8);a=p.parse_args()
    data=json.loads(a.input.read_text(encoding='utf-8'));rows=data.get('products',[]);out=[];logs=[]
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs=[ex.submit(audit,dict(r)) for r in rows]
        for f in as_completed(futs):r,l=f.result();out.append(r);logs.extend(l)
    payload={'shard':data.get('shard'),'inputCount':len(rows),'unicornCount':sum(r.get('isUnicorn') for r in out),'kcFound':sum(bool(r.get('kcNumber')) for r in out),'products':out,'logs':logs,'generatedAt':now()}
    a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8');print({k:payload[k] for k in ('shard','inputCount','unicornCount','kcFound')},flush=True)
if __name__=='__main__':main()
