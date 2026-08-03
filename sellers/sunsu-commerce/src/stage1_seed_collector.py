#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, re
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs
from playwright.sync_api import sync_playwright

BASE='https://www.coupang.com'
SELLER='순수커머스'
EXPECTED=195
SEEDS=['1329308694','274824520']
PRODUCT_RE=re.compile(r'/vp/products/(\d+)')
UNICORN_RE=re.compile(r'(?:\(주\)\s*유니콘|주식회사\s*유니콘|BOOKFRIENDS|UNICORN|(?<![가-힣])유니콘(?![가-힣]))',re.I)

@dataclass
class Product:
    productId:str
    itemId:str=''
    vendorItemId:str=''
    productName:str=''
    productUrl:str=''
    publisherManufacturer:str=''
    publisherGrade:str='미확정'
    publisherReason:str=''
    evidenceUrls:list[str]|None=None
    checkedAt:str=''

def now(): return datetime.now(timezone.utc).isoformat()

def add(store,url,evidence,name=''):
    m=PRODUCT_RE.search(url or '')
    if not m:return
    pid=m.group(1); q=parse_qs(urlparse(url).query)
    p=store.setdefault(pid,Product(pid,evidenceUrls=[]))
    p.productUrl=p.productUrl or urljoin(BASE,url)
    p.itemId=p.itemId or (q.get('itemId') or [''])[0]
    p.vendorItemId=p.vendorItemId or (q.get('vendorItemId') or [''])[0]
    p.productName=p.productName or name.strip()
    p.evidenceUrls=sorted(set((p.evidenceUrls or [])+[evidence]))

def extract(text):
    out=set()
    for m in re.finditer(r'https?://(?:www\.)?coupang\.com/vp/products/\d+[^\"\'<>\s]*',text or ''): out.add(m.group(0).replace('&amp;','&'))
    for m in re.finditer(r'/vp/products/\d+[^\"\'<>\s]*',text or ''): out.add(urljoin(BASE,m.group(0).replace('&amp;','&')))
    return out

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--out',type=Path,required=True); ap.add_argument('--confirmed-out',type=Path,required=True); a=ap.parse_args()
    store={}; log=[]; seller_urls=[]
    with sync_playwright() as pw:
        b=pw.chromium.launch(headless=True,args=['--no-sandbox','--disable-dev-shm-usage','--disable-blink-features=AutomationControlled'])
        c=b.new_context(locale='ko-KR',timezone_id='Asia/Seoul',viewport={'width':1440,'height':1200},user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/127 Safari/537.36')
        c.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        p=c.new_page()
        def resp(r):
            try:
                body=r.text()
                if len(body)<12000000:
                    for u in extract(body): add(store,u,r.url)
                    if SELLER in body and ('shop.coupang.com' in body or '/vendors/' in body):
                        for m in re.finditer(r'https?://shop\.coupang\.com/[A-Za-z0-9_-]+[^\"\'<>\s]*',body): seller_urls.append(m.group(0))
            except: pass
        p.on('response',resp)
        for pid in SEEDS:
            u=f'{BASE}/vp/products/{pid}'
            try:
                p.goto(u,wait_until='networkidle',timeout=60000); p.wait_for_timeout(2000)
                html=p.content(); body=p.locator('body').inner_text(timeout=5000)
                add(store,u,'verified-seed')
                for x in extract(html): add(store,x,u)
                for href in p.locator('a[href]').evaluate_all('els=>els.map(e=>e.href)'):
                    if 'shop.coupang.com' in href or '/vp/products/' in href:
                        if 'shop.coupang.com' in href and (SELLER in body or SELLER in html): seller_urls.append(href)
                        add(store,href,u)
                log.append({'seed':pid,'title':p.title(),'sellerSeen':SELLER in body or SELLER in html,'sellerUrls':sorted(set(seller_urls)),'count':len(store)})
            except Exception as e: log.append({'seed':pid,'error':f'{type(e).__name__}: {e}'})
        # Prefer dynamically discovered seller URL; retain historical ID only as fallback.
        targets=list(dict.fromkeys(seller_urls+['https://shop.coupang.com/A01593407?locale=ko_KR&platform=p']))
        for target in targets:
            try:
                p.goto(target,wait_until='domcontentloaded',timeout=60000); p.wait_for_timeout(3000)
                for _ in range(80):
                    p.mouse.wheel(0,1800); p.wait_for_timeout(350)
                    for sel in ["button:has-text('더보기')","a:has-text('다음')"]:
                        try:
                            x=p.locator(sel).first
                            if x.is_visible(timeout=50): x.click(timeout=300)
                        except: pass
                html=p.content()
                for x in extract(html): add(store,x,target)
                for href in p.locator("a[href*='/vp/products/']").evaluate_all('els=>els.map(e=>e.href)'): add(store,href,target)
                log.append({'target':target,'title':p.title(),'bodyPrefix':p.locator('body').inner_text(timeout=5000)[:500],'count':len(store)})
            except Exception as e: log.append({'target':target,'error':f'{type(e).__name__}: {e}','count':len(store)})
        # Verify seller and classify publisher.
        for i,(pid,x) in enumerate(list(store.items())):
            try:
                p.goto(x.productUrl or f'{BASE}/vp/products/{pid}',wait_until='domcontentloaded',timeout=35000); p.wait_for_timeout(700)
                body=p.locator('body').inner_text(timeout=5000); html=p.content()
                if SELLER not in body and SELLER not in html and 'verified-seed' not in (x.evidenceUrls or []):
                    del store[pid]; continue
                try: x.productName=p.locator('h1').first.inner_text(timeout=1500).strip()
                except: pass
                m=re.search(r'(?:출판사|제조사|제조자\s*\(수입자\))\s*[:：]?\s*([^\n|]{1,100})',body,re.I)
                if m:x.publisherManufacturer=re.sub(r'\s+',' ',m.group(1)).strip()
                text=' '.join([x.productName,x.publisherManufacturer,body[:12000]])
                if UNICORN_RE.search(text): x.publisherGrade='확정'; x.publisherReason='쿠팡 직접표기'
                elif any(k in x.productName for k in ['퍼즐','스티커','색칠북','컬렉션북']): x.publisherGrade='후보'; x.publisherReason='공식 도서정보 교차검증 필요'
                x.checkedAt=now()
            except Exception: x.checkedAt=now()
            if i%20==0: print({'verified':i,'remaining':len(store)},flush=True)
        b.close()
    rows=[asdict(v) for v in sorted(store.values(),key=lambda z:int(z.productId))]
    candidates=[r for r in rows if r['publisherGrade'] in ('확정','후보')]
    payload={'seller':{'name':SELLER,'expectedCount':EXPECTED},'summary':{'discoveredUniqueProductIds':len(rows),'expectedCount':EXPECTED,'catalogComplete':len(rows)==EXPECTED,'publisherCandidates':len(candidates)},'sourceLog':log,'products':rows,'generatedAt':now()}
    a.out.parent.mkdir(parents=True,exist_ok=True); a.out.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8'); a.confirmed_out.write_text(json.dumps(candidates,ensure_ascii=False,indent=2),encoding='utf-8')
    if len(rows)!=EXPECTED: raise SystemExit(f'stage1 incomplete: expected {EXPECTED}, discovered {len(rows)}')
if __name__=='__main__': main()
