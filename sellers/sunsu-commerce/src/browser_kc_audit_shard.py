#!/usr/bin/env python3
from __future__ import annotations
import argparse, html, json, re, time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus, urlparse
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

UNICORN_RE = re.compile(r'(?:\(주\)\s*유니콘|주식회사\s*유니콘|BOOKFRIENDS|북프렌즈|(?<![A-Za-z])UNICORN(?![A-Za-z])|(?<![가-힣])유니콘(?![가-힣]))', re.I)
KC_PATTERNS = [
    re.compile(r'(?<![A-Z0-9])(?:CB|CA|SU)\d{2,4}[A-Z0-9]{1,6}-\d{4}[A-Z]?(?![A-Z0-9])', re.I),
    re.compile(r'(?<![A-Z0-9])R-[RCLS]-[A-Z0-9]{2,14}-[A-Z0-9-]{2,16}(?![A-Z0-9])', re.I),
]
EXCLUDED = {'U003E1577-7011'}

def now(): return datetime.now(timezone.utc).isoformat()

def candidates(text: str) -> list[str]:
    text = html.unescape(text or '').replace('\\u002D', '-').replace('\\/', '/')
    out=[]
    for pat in KC_PATTERNS:
        for value in pat.findall(text):
            value=value.upper().strip('.,:;()[]{}')
            if value not in EXCLUDED and value not in out: out.append(value)
    return out

def is_unicorn(row: dict) -> bool:
    direct=' '.join(str(row.get(k,'')) for k in ('productName','publisherManufacturer','manufacturer','publisher','brand'))
    return bool(UNICORN_RE.search(direct))

def normalized_queries(name: str) -> list[str]:
    base=re.sub(r'[\[\]\(\),/|]',' ',name or '')
    base=re.sub(r'\s+',' ',base).strip()
    parts=[base]
    for token in ('유니콘','BOOKFRIENDS','북프렌즈'):
        parts.append(base.replace(token,' ').strip())
    words=[w for w in base.split() if len(w)>=2]
    if len(words)>5: parts.append(' '.join(words[:5]))
    return list(dict.fromkeys(x for x in parts if len(x)>=3))

def title_tokens(name: str) -> list[str]:
    stop={'유니콘','페이퍼백','컬렉션북','스티커','색칠북','도서'}
    return [w for w in re.sub(r'[^0-9A-Za-z가-힣 ]',' ',name or '').split() if len(w)>=2 and w not in stop][:8]

def rendered_text(page, url: str) -> tuple[str,int,str]:
    try:
        response=page.goto(url,wait_until='domcontentloaded',timeout=22000)
        page.wait_for_timeout(1300)
        text=page.content()+' '+page.locator('body').inner_text(timeout=6000)
        return text,(response.status if response else 0),'ok'
    except PlaywrightTimeout:
        return '',0,'timeout'
    except Exception as e:
        return '',0,type(e).__name__

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--input',type=Path,required=True); ap.add_argument('--out',type=Path,required=True); a=ap.parse_args()
    data=json.loads(a.input.read_text(encoding='utf-8')); rows=data.get('products',[]); logs=[]; output=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,args=['--disable-blink-features=AutomationControlled','--no-sandbox'])
        context=browser.new_context(locale='ko-KR',timezone_id='Asia/Seoul',user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36')
        context.set_default_timeout(12000)
        page=context.new_page()
        for row0 in rows:
            row=dict(row0); unicorn=is_unicorn(row); row['isUnicorn']=unicorn
            row['publisherGrade']='확정' if unicorn else '제외'; row['publisherReason']='수집 필드 직접 표기' if unicorn else ''
            if not unicorn: output.append(row); continue

            pid=str(row.get('productId') or ''); item=str(row.get('itemId') or ''); vendor=str(row.get('vendorItemId') or '')
            name=(row.get('productName') or '').strip(); tokens=title_tokens(name)
            found=[]; source=''; evidence=[]; source_hits=defaultdict(set); title_hits=defaultdict(int)

            coupang_urls=[]
            if row.get('productUrl'): coupang_urls.append(row['productUrl'])
            if pid: coupang_urls += [f'https://www.coupang.com/vp/products/{pid}?itemId={item}&vendorItemId={vendor}',f'https://m.coupang.com/vm/products/{pid}?itemId={item}&vendorItemId={vendor}']
            for url in dict.fromkeys(coupang_urls):
                body,status,state=rendered_text(page,url); cs=candidates(body)
                logs.append({'productId':pid,'stage':'browser-coupang','url':url,'http':status,'state':state,'found':cs,'checkedAt':now()})
                for c in cs:
                    if c not in found: found.append(c)
                    source_hits[c].add('coupang.com')
                    title_hits[c]=max(title_hits[c],sum(t.lower() in body.lower() for t in tokens))
                if cs: source=url; evidence.append({'source':url,'candidates':cs})
                time.sleep(0.5)

            if name:
                for q in normalized_queries(name):
                    urls=[
                        'https://search.naver.com/search.naver?query='+quote_plus(f'"{q}" "KC 인증번호"'),
                        'https://www.google.com/search?q='+quote_plus(f'"{q}" "KC 인증번호"'),
                        'https://www.bing.com/search?q='+quote_plus(f'"{q}" "KC 인증번호"'),
                        'https://www.safetykorea.kr/release/certificationsearch?searchType=product&searchWord='+quote_plus(q),
                        'https://www.safetykorea.kr/release/certificationsearch?modelName='+quote_plus(q),
                    ]
                    for url in urls:
                        body,status,state=rendered_text(page,url); cs=candidates(body); domain=urlparse(url).netloc.lower()
                        logs.append({'productId':pid,'stage':'browser-search','query':q,'url':url,'http':status,'state':state,'found':cs,'checkedAt':now()})
                        if cs: evidence.append({'source':url,'query':q,'candidates':cs})
                        for c in cs:
                            if c not in found: found.append(c)
                            source_hits[c].add(domain)
                            title_hits[c]=max(title_hits[c],sum(t.lower() in body.lower() for t in tokens))
                        time.sleep(0.35)
                    if found: break

            verified=''; verified_source=''; confidence=''
            for kc in found:
                exact_urls=[
                    'https://www.safetykorea.kr/release/certificationsearch?certNum='+quote_plus(kc),
                    'https://www.safetykorea.kr/release/certificationSearch?certNum='+quote_plus(kc),
                    'https://search.naver.com/search.naver?query='+quote_plus(f'"{kc}" "{name}"'),
                    'https://www.google.com/search?q='+quote_plus(f'"{kc}" "{name}"'),
                    'https://www.bing.com/search?q='+quote_plus(f'"{kc}" "{name}"'),
                ]
                for url in exact_urls:
                    body,status,state=rendered_text(page,url); domain=urlparse(url).netloc.lower(); exact=kc in body.upper(); overlap=sum(t.lower() in body.lower() for t in tokens)
                    logs.append({'productId':pid,'stage':'browser-kc-exact','kc':kc,'url':url,'http':status,'state':state,'exact':exact,'titleTokenHits':overlap,'checkedAt':now()})
                    if exact:
                        source_hits[kc].add(domain); title_hits[kc]=max(title_hits[kc],overlap)
                        evidence.append({'source':url,'kc':kc,'exact':True,'titleTokenHits':overlap})
                        if 'safetykorea.kr' in domain:
                            verified=kc; verified_source=url; confidence='official-exact'; break
                    time.sleep(0.35)
                if verified: break
                independent={d for d in source_hits[kc] if any(x in d for x in ('naver.com','google.com','bing.com','coupang.com','safetykorea.kr'))}
                if len(independent)>=2 and title_hits[kc]>=1:
                    verified=kc; verified_source='independent exact search evidence'; confidence='multi-source-high-confidence'; break

            row['kcCandidates']=found
            row['kcEvidence']=evidence
            row['kcNumber']=verified
            row['kcConfidence']=confidence
            row['kcSource']=verified_source if verified else (source if source else 'exact 근거 없음')
            if confidence=='official-exact': row['kcStatus']='KC 적합-공식조회 일치'
            elif confidence=='multi-source-high-confidence': row['kcStatus']='KC 번호 고신뢰-공식상태 미확인'
            else: row['kcStatus']='후보 발견-공식 미확정' if found else 'KC 공개표기 없음'
            output.append(row)
        context.close(); browser.close()
    payload={'shard':data.get('shard'),'inputCount':len(rows),'unicornCount':sum(bool(r.get('isUnicorn')) for r in output),'kcFound':sum(bool(r.get('kcNumber')) for r in output),'kcOfficial':sum(r.get('kcConfidence')=='official-exact' for r in output),'kcHighConfidence':sum(r.get('kcConfidence')=='multi-source-high-confidence' for r in output),'kcCandidateListings':sum(bool(r.get('kcCandidates')) for r in output),'products':output,'logs':logs,'generatedAt':now()}
    a.out.parent.mkdir(parents=True,exist_ok=True); a.out.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8'); print({k:payload[k] for k in ('shard','inputCount','unicornCount','kcFound','kcOfficial','kcHighConfidence','kcCandidateListings')})
if __name__=='__main__': main()
