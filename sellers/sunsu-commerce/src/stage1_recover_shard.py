#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, re, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse, parse_qs
import requests
from playwright.sync_api import sync_playwright

SELLER='순수커머스'
SELLER_ID='A01593407'
BASE='https://www.coupang.com'
PRODUCT_RE=re.compile(r'(?:https?://(?:www\.|m\.)?coupang\.com)?/vp/products/(\d+)([^\"\'<>\s]*)',re.I)
UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/127 Safari/537.36'
TERMS=[
 '순수커머스','순수커머스 퍼즐','순수커머스 색칠북','순수커머스 스티커북','순수커머스 판퍼즐',
 '순수커머스 캐치티니핑','순수커머스 뽀로로','순수커머스 산리오','순수커머스 타요',
 '유니콘 판퍼즐','유니콘 색칠북','BOOKFRIENDS 퍼즐','UNICORN 퍼즐',
 '스티커 퀸 300','워터색칠북','대판퍼즐','퍼즐 2종세트','스티커 컬렉션북'
]

def now(): return datetime.now(timezone.utc).isoformat()

def add(store,url,source,title=''):
    m=PRODUCT_RE.search(url or '')
    if not m:return
    pid=m.group(1); full=urljoin(BASE,m.group(0)); q=parse_qs(urlparse(full).query)
    row=store.setdefault(pid,{'productId':pid,'itemId':'','vendorItemId':'','productName':'','productUrl':f'{BASE}/vp/products/{pid}','sources':[]})
    row['itemId']=row['itemId'] or (q.get('itemId') or [''])[0]
    row['vendorItemId']=row['vendorItemId'] or (q.get('vendorItemId') or [''])[0]
    row['productName']=row['productName'] or re.sub(r'\s+',' ',title).strip()
    row['sources']=sorted(set(row['sources']+[source]))

def extract_text(store,text,source):
    for m in PRODUCT_RE.finditer(text or ''): add(store,m.group(0),source)
    try:
        obj=json.loads(text)
    except Exception:
        return
    def walk(x):
        if isinstance(x,dict):
            pid=x.get('productId') or x.get('productID')
            if pid:
                url=f"{BASE}/vp/products/{pid}"
                item=x.get('itemId') or ''
                vendor=x.get('vendorItemId') or ''
                if item or vendor:url+=f'?itemId={item}&vendorItemId={vendor}'
                name=x.get('productName') or x.get('name') or x.get('title') or ''
                add(store,url,source,str(name))
            for v in x.values(): walk(v)
        elif isinstance(x,list):
            for v in x: walk(v)
        elif isinstance(x,str):
            for m in PRODUCT_RE.finditer(x): add(store,m.group(0),source)
    walk(obj)

def http_search(store,session,query,source):
    urls=[
      f'https://www.bing.com/search?q={quote("site:coupang.com/vp/products "+query)}&count=50',
      f'https://html.duckduckgo.com/html/?q={quote("site:coupang.com/vp/products "+query)}',
      f'{BASE}/np/search?q={quote(query)}&channel=user&page=1',
      f'https://m.coupang.com/nm/search?q={quote(query)}&page=1'
    ]
    for u in urls:
        try:
            r=session.get(u,timeout=25,headers={'User-Agent':UA,'Accept-Language':'ko-KR,ko;q=0.9'})
            extract_text(store,r.text,u)
        except Exception: pass

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--shard',type=int,required=True); ap.add_argument('--shards',type=int,default=8); ap.add_argument('--out',type=Path,required=True); a=ap.parse_args()
    store={}; events=[]; session=requests.Session()
    selected=[t for i,t in enumerate(TERMS) if i%a.shards==a.shard]
    for t in selected:http_search(store,session,t,f'http-search:{t}')
    targets=[
      f'https://shop.coupang.com/{SELLER_ID}?locale=ko_KR&platform=p',
      f'https://shop.coupang.com/{SELLER_ID}?locale=ko_KR&platform=m',
      f'{BASE}/vp/vendors/{SELLER_ID}',
      f'https://m.coupang.com/vm/vendors/{SELLER_ID}'
    ]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,args=['--no-sandbox','--disable-dev-shm-usage','--disable-blink-features=AutomationControlled'])
        ctx=browser.new_context(locale='ko-KR',timezone_id='Asia/Seoul',user_agent=UA,viewport={'width':1440,'height':1200})
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page=ctx.new_page(); captured=[]
        def on_response(resp):
            u=resp.url
            if any(k in u.lower() for k in ['product','vendor','seller','shop','search','category','page','list']):
                try:
                    body=resp.text()
                    if len(body)<=15000000:
                        before=len(store); extract_text(store,body,u)
                        if len(store)>before or 'api' in u.lower(): captured.append({'url':u,'status':resp.status,'added':len(store)-before})
                except Exception: pass
        page.on('response',on_response)
        for target in targets:
            try:
                page.goto(target,wait_until='domcontentloaded',timeout=60000); page.wait_for_timeout(2500)
                for n in range(100):
                    page.mouse.wheel(0,2200); page.wait_for_timeout(180)
                    for sel in ["button:has-text('더보기')","a:has-text('다음')","button[aria-label*='다음']"]:
                        try:
                            x=page.locator(sel).first
                            if x.is_visible(timeout=30):x.click(timeout=250)
                        except Exception:pass
                extract_text(store,page.content(),target)
                for href in page.locator('a[href]').evaluate_all('els=>els.map(e=>e.href)'):
                    add(store,href,target)
                events.append({'target':target,'title':page.title(),'count':len(store)})
            except Exception as e:events.append({'target':target,'error':f'{type(e).__name__}: {e}','count':len(store)})
        # Coupang search pages assigned to this shard; browser network often exposes richer JSON than requests.
        for term in selected:
            for pg in range(1,11):
                for host in ['www','m']:
                    u=(f'https://www.coupang.com/np/search?q={quote(term)}&channel=user&page={pg}' if host=='www' else f'https://m.coupang.com/nm/search?q={quote(term)}&page={pg}')
                    try:
                        page.goto(u,wait_until='domcontentloaded',timeout=35000); page.wait_for_timeout(700)
                        extract_text(store,page.content(),u)
                    except Exception: pass
        browser.close()
    rows=sorted(store.values(),key=lambda x:int(x['productId']))
    payload={'seller':SELLER,'sellerId':SELLER_ID,'shard':a.shard,'shards':a.shards,'count':len(rows),'terms':selected,'events':events,'capturedResponses':captured[-500:],'products':rows,'generatedAt':now()}
    a.out.parent.mkdir(parents=True,exist_ok=True); a.out.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    print({'shard':a.shard,'count':len(rows),'terms':selected},flush=True)
if __name__=='__main__':main()
