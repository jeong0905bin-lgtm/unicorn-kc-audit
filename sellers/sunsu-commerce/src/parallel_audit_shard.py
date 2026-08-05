#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,re
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import quote_plus
import requests
from bs4 import BeautifulSoup

UNICORN_RE=re.compile(r'(?:\(주\)\s*유니콘|주식회사\s*유니콘|BOOKFRIENDS|(?<![A-Za-z])UNICORN(?![A-Za-z])|(?<![가-힣])유니콘(?![가-힣]))',re.I)
# Korean KC certificate numbers commonly begin CB/CA/SU and may contain letters, digits and hyphens.
KC_RE=re.compile(r'(?<![A-Z0-9])(?:CB|CA|SU)\d{2,}[A-Z0-9-]{5,}(?![A-Z0-9])',re.I)
EXCLUDED={'U003E1577-7011'}
HEADERS={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36','Accept-Language':'ko-KR,ko;q=0.9,en;q=0.7'}
def now():return datetime.now(timezone.utc).isoformat()
def fetch(url):
    try:
        r=requests.get(url,headers=HEADERS,timeout=16,allow_redirects=True)
        raw=r.text or ''
        txt=BeautifulSoup(raw,'html.parser').get_text(' ',strip=True)
        return raw+' '+txt,('ok' if r.ok else f'http_{r.status_code}')
    except Exception as e:return '',f'{type(e).__name__}'
def kc_candidates(text):
    out=[]
    for c in KC_RE.findall(text or ''):
        c=c.upper().strip('.,:;()[]{}')
        if c not in EXCLUDED and c not in out:out.append(c)
    return out
def publisher_direct(row):
    # Use explicit publisher/manufacturer fields and title patterns, not generic raw JSON noise alone.
    fields=' '.join(str(row.get(k,'')) for k in ('publisherManufacturer','brand','manufacturer','publisher'))
    title=str(row.get('productName',''))
    explicit=bool(UNICORN_RE.search(fields))
    title_explicit=bool(re.search(r'(?:,|\s)(?:\(주\)\s*유니콘|주식회사\s*유니콘|유니콘)(?:,|\s|$)',title,re.I))
    return explicit or title_explicit

def audit(row):
    logs=[]
    raw=json.dumps(row.get('rawListing') or {},ensure_ascii=False)
    direct=publisher_direct(row)
    unicorn=direct
    reason='쿠팡 상품명/출판사·제조사 직접 표기' if direct else ''
    name=(row.get('productName') or '').strip()
    if not unicorn and name:
        for base in ('https://search.kyobobook.co.kr/search?keyword=','https://www.yes24.com/Product/Search?domain=ALL&query=','https://www.aladin.co.kr/search/wsearchresult.aspx?SearchTarget=All&SearchWord='):
            url=base+quote_plus(name); page,state=fetch(url); hit=bool(UNICORN_RE.search(page)); logs.append({'productId':row.get('productId'),'stage':'publisher','url':url,'state':state,'hit':hit,'checkedAt':now()})
            if hit: unicorn=True;reason='공식 도서검색 유니콘 표기';break
    row['isUnicorn']=unicorn;row['publisherGrade']='확정' if direct else ('고신뢰 추론' if unicorn else '제외');row['publisherReason']=reason
    if not unicorn:return row,logs

    found=[];sources=[]
    # 1) Search every field already returned by the seller/listing API.
    for c in kc_candidates(json.dumps(row,ensure_ascii=False)):
        found.append(c);sources.append('쿠팡 목록 API 메타데이터')

    # 2) Query several product-detail variants because Coupang serves different payloads by URL/device.
    pid=str(row.get('productId') or '')
    item=str(row.get('itemId') or '')
    vendor=str(row.get('vendorItemId') or '')
    urls=[]
    if row.get('productUrl'):urls.append(row['productUrl'])
    if pid:
        urls.extend([
            f'https://www.coupang.com/vp/products/{pid}?itemId={item}&vendorItemId={vendor}',
            f'https://www.coupang.com/vp/products/{pid}',
            f'https://m.coupang.com/vm/products/{pid}?itemId={item}&vendorItemId={vendor}',
        ])
    for url in dict.fromkeys(urls):
        page,state=fetch(url); cs=kc_candidates(page)
        logs.append({'productId':pid,'stage':'kc-coupang','url':url,'state':state,'found':cs,'checkedAt':now()})
        for c in cs:
            if c not in found:found.append(c);sources.append(url)
        if found:break

    # 3) SafetyKorea keyword/model searches. Keep only certificate-shaped exact strings returned in results.
    if not found and name:
        queries=[name,re.sub(r'[,\(\)\[\]]',' ',name).split(' 유니콘')[0].strip()]
        for q in dict.fromkeys(x for x in queries if len(x)>=3):
            surls=[
                'https://www.safetykorea.kr/release/certificationsearch?searchType=product&searchWord='+quote_plus(q),
                'https://www.safetykorea.kr/release/certificationsearch?modelName='+quote_plus(q),
                'https://www.safetykorea.kr/release/certificationsearch?productName='+quote_plus(q),
            ]
            for surl in surls:
                page,state=fetch(surl);cs=kc_candidates(page)
                logs.append({'productId':pid,'stage':'kc-safetykorea-keyword','url':surl,'state':state,'found':cs,'checkedAt':now()})
                for c in cs:
                    if c not in found:found.append(c);sources.append(surl)
                if found:break
            if found:break

    kc=found[0] if found else ''
    row['kcNumber']=kc
    row['kcCandidates']=found
    row['kcSource']=sources[0] if sources else 'exact 근거 없음'
    row['kcStatus']='미검증' if kc else 'KC 공개표기 없음'
    if kc:
        surl=f'https://www.safetykorea.kr/release/certificationsearch?certNum={quote_plus(kc)}';spage,sstate=fetch(surl);exact=kc in spage.upper();expired=bool(re.search(r'기간\s*만료|만료|취소|효력\s*상실',spage)) if exact else False
        row['kcStatus']='KC 기간만료' if expired else ('KC 적합' if exact else '검증 불가');row['expired']=expired
        logs.append({'productId':pid,'stage':'safetykorea-exact','url':surl,'state':sstate,'exact':exact,'expired':expired,'checkedAt':now()})
    return row,logs

def main():
    p=argparse.ArgumentParser();p.add_argument('--input',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--workers',type=int,default=8);a=p.parse_args()
    data=json.loads(a.input.read_text(encoding='utf-8'));rows=data.get('products',[]);out=[];logs=[]
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs=[ex.submit(audit,dict(r)) for r in rows]
        for f in as_completed(futs):r,l=f.result();out.append(r);logs.extend(l)
    payload={'shard':data.get('shard'),'inputCount':len(rows),'unicornCount':sum(bool(r.get('isUnicorn')) for r in out),'kcFound':sum(bool(r.get('kcNumber')) for r in out),'products':out,'logs':logs,'generatedAt':now()}
    a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8');print({k:payload[k] for k in ('shard','inputCount','unicornCount','kcFound')},flush=True)
if __name__=='__main__':main()
