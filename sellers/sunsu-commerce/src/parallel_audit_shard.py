#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,re,html
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import quote_plus
import requests
from bs4 import BeautifulSoup

UNICORN_RE=re.compile(r'(?:\(주\)\s*유니콘|주식회사\s*유니콘|BOOKFRIENDS|(?<![A-Za-z])UNICORN(?![A-Za-z])|(?<![가-힣])유니콘(?![가-힣]))',re.I)
# Strict certificate shapes only. Reject search-engine hashes/tracking IDs.
KC_PATTERNS=[
 re.compile(r'(?<![A-Z0-9])(?:CB|CA|SU)\d{3}[A-Z]\d{3,4}-\d{4}[A-Z]?(?![A-Z0-9])',re.I),
 re.compile(r'(?<![A-Z0-9])(?:CB|CA|SU)\d{2,4}[A-Z0-9]{1,5}-\d{4}[A-Z]?(?![A-Z0-9])',re.I),
 re.compile(r'(?<![A-Z0-9])R-[RCLS]-[A-Z0-9]{3,12}-[A-Z0-9-]{2,12}(?![A-Z0-9])',re.I),
]
EXCLUDED={'U003E1577-7011'}
HEADERS={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36','Accept-Language':'ko-KR,ko;q=0.9,en;q=0.7','Referer':'https://www.google.com/'}
def now():return datetime.now(timezone.utc).isoformat()
def fetch(url):
    try:
        r=requests.get(url,headers=HEADERS,timeout=18,allow_redirects=True)
        raw=r.text or ''
        decoded=html.unescape(raw).replace('\\u002D','-').replace('\\/','/')
        txt=BeautifulSoup(decoded,'html.parser').get_text(' ',strip=True)
        return decoded+' '+txt,('ok' if r.ok else f'http_{r.status_code}')
    except Exception as e:return '',type(e).__name__
def kc_candidates(text):
    out=[]
    src=text or ''
    for pat in KC_PATTERNS:
        for c in pat.findall(src):
            c=c.upper().strip('.,:;()[]{}')
            if 10<=len(c)<=24 and c not in EXCLUDED and c not in out:out.append(c)
    return out
def publisher_direct(row):
    fields=' '.join(str(row.get(k,'')) for k in ('publisherManufacturer','brand','manufacturer','publisher'))
    title=str(row.get('productName',''))
    return bool(UNICORN_RE.search(fields) or re.search(r'(?:,|\s)(?:\(주\)\s*유니콘|주식회사\s*유니콘|유니콘)(?:,|\s|$)',title,re.I))
def normalized_queries(name):
    base=re.sub(r'[\[\]\(\),/|]',' ',name);base=re.sub(r'\s+',' ',base).strip();parts=[base]
    for token in ('유니콘','BOOKFRIENDS','북프렌즈'):parts.append(base.replace(token,' ').strip())
    words=[w for w in base.split() if len(w)>=2]
    if len(words)>3:parts.append(' '.join(words[:4]))
    return list(dict.fromkeys(x for x in parts if len(x)>=3))
def audit(row):
    logs=[];direct=publisher_direct(row);unicorn=direct;reason='쿠팡 상품명/출판사·제조사 직접 표기' if direct else ''
    name=(row.get('productName') or '').strip();pid=str(row.get('productId') or '')
    if not unicorn and name:
        for base in ('https://search.kyobobook.co.kr/search?keyword=','https://www.yes24.com/Product/Search?domain=ALL&query=','https://www.aladin.co.kr/search/wsearchresult.aspx?SearchTarget=All&SearchWord='):
            url=base+quote_plus(name);page,state=fetch(url);hit=bool(UNICORN_RE.search(page));logs.append({'productId':pid,'stage':'publisher','url':url,'state':state,'hit':hit,'checkedAt':now()})
            if hit:unicorn=True;reason='공식 도서검색 유니콘 표기';break
    row['isUnicorn']=unicorn;row['publisherGrade']='확정' if direct else ('고신뢰 추론' if unicorn else '제외');row['publisherReason']=reason
    if not unicorn:return row,logs
    found=[];sources=[]
    def add(cs,src):
        for c in cs:
            if c not in found:found.append(c);sources.append(src)
    add(kc_candidates(json.dumps(row,ensure_ascii=False)),'수집 메타데이터')
    item=str(row.get('itemId') or '');vendor=str(row.get('vendorItemId') or '')
    urls=[]
    if row.get('productUrl'):urls.append(row['productUrl'])
    if pid:urls += [f'https://www.coupang.com/vp/products/{pid}?itemId={item}&vendorItemId={vendor}',f'https://m.coupang.com/vm/products/{pid}?itemId={item}&vendorItemId={vendor}',f'https://www.coupang.com/vp/products/{pid}']
    for url in dict.fromkeys(urls):
        page,state=fetch(url);cs=kc_candidates(page);logs.append({'productId':pid,'stage':'kc-coupang','url':url,'state':state,'found':cs,'checkedAt':now()});add(cs,url)
    if not found and name:
        for q in normalized_queries(name):
            for url in (
                'https://search.naver.com/search.naver?query='+quote_plus(f'"{q}" KC 인증번호'),
                'https://www.google.com/search?q='+quote_plus(f'"{q}" "KC 인증번호"'),
                'https://www.bing.com/search?q='+quote_plus(f'"{q}" "KC 인증번호"'),
                'https://www.safetykorea.kr/release/certificationsearch?searchType=product&searchWord='+quote_plus(q),
                'https://www.safetykorea.kr/release/certificationsearch?modelName='+quote_plus(q),
            ):
                page,state=fetch(url);cs=kc_candidates(page);logs.append({'productId':pid,'stage':'kc-search','query':q,'url':url,'state':state,'found':cs,'checkedAt':now()});add(cs,url)
            if found:break
    verified=[]
    for kc in found:
        surl='https://www.safetykorea.kr/release/certificationsearch?certNum='+quote_plus(kc);page,state=fetch(surl);exact=kc in page.upper();expired=bool(re.search(r'기간\s*만료|만료|취소|효력\s*상실',page)) if exact else False
        logs.append({'productId':pid,'stage':'safetykorea-exact','url':surl,'state':state,'kc':kc,'exact':exact,'expired':expired,'checkedAt':now()})
        if exact:verified.append((kc,expired,surl));break
    if verified:
        kc,expired,surl=verified[0];row['kcNumber']=kc;row['kcCandidates']=found;row['kcSource']=surl;row['kcStatus']='KC 기간만료' if expired else 'KC 적합';row['expired']=expired
    else:
        row['kcNumber']='';row['kcCandidates']=found;row['kcSource']='exact SafetyKorea 근거 없음';row['kcStatus']='KC 공개표기 없음'
    return row,logs
def main():
    p=argparse.ArgumentParser();p.add_argument('--input',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--workers',type=int,default=12);a=p.parse_args()
    data=json.loads(a.input.read_text(encoding='utf-8'));rows=data.get('products',[]);out=[];logs=[]
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs=[ex.submit(audit,dict(r)) for r in rows]
        for f in as_completed(futs):r,l=f.result();out.append(r);logs.extend(l)
    payload={'shard':data.get('shard'),'inputCount':len(rows),'unicornCount':sum(bool(r.get('isUnicorn')) for r in out),'kcFound':sum(bool(r.get('kcNumber')) for r in out),'products':out,'logs':logs,'generatedAt':now()}
    a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8');print({k:payload[k] for k in ('shard','inputCount','unicornCount','kcFound')},flush=True)
if __name__=='__main__':main()
