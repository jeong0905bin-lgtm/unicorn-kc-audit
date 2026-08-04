#!/usr/bin/env python3
from __future__ import annotations
import argparse, copy, json, re, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse, parse_qs
import requests
from playwright.sync_api import sync_playwright

SELLER='순수커머스'; SELLER_ID='A01593407'; STORE_ID=297717
BASE='https://www.coupang.com'; SHOP='https://shop.coupang.com'
PRODUCT_RE=re.compile(r'(?:https?://(?:www\.|m\.)?coupang\.com)?/vp/products/(\d+)([^\"\'<>\s]*)',re.I)
UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/127 Safari/537.36'
TERMS=['순수커머스','순수커머스 퍼즐','순수커머스 색칠북','순수커머스 스티커북','순수커머스 판퍼즐','순수커머스 캐치티니핑','순수커머스 뽀로로','순수커머스 산리오','순수커머스 타요','유니콘 판퍼즐','유니콘 색칠북','BOOKFRIENDS 퍼즐','UNICORN 퍼즐','스티커 퀸 300','워터색칠북','대판퍼즐','퍼즐 2종세트','스티커 컬렉션북']
PAGE_KEYS={'page','pageNo','pageNum','pageNumber','currentPage','pageIndex'}
OFFSET_KEYS={'offset','start','from'}
SIZE_KEYS={'size','pageSize','limit','count','rowsPerPage'}

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
    try: obj=json.loads(text)
    except Exception: return
    def walk(x):
        if isinstance(x,dict):
            pid=x.get('productId') or x.get('productID')
            if pid:
                item=x.get('itemId') or ''; vendor=x.get('vendorItemId') or ''
                u=f'{BASE}/vp/products/{pid}' + (f'?itemId={item}&vendorItemId={vendor}' if item or vendor else '')
                add(store,u,source,str(x.get('productName') or x.get('name') or x.get('title') or ''))
            for v in x.values(): walk(v)
        elif isinstance(x,list):
            for v in x: walk(v)
        elif isinstance(x,str):
            for m in PRODUCT_RE.finditer(x): add(store,m.group(0),source)
    walk(obj)

def http_search(store,session,query):
    for pg in range(1,6):
        urls=[f'https://www.bing.com/search?q={quote("site:coupang.com/vp/products "+query)}&count=50&first={1+(pg-1)*50}',f'https://html.duckduckgo.com/html/?q={quote("site:coupang.com/vp/products "+query)}&s={(pg-1)*30}',f'{BASE}/np/search?q={quote(query)}&channel=user&page={pg}',f'https://m.coupang.com/nm/search?q={quote(query)}&page={pg}']
        for u in urls:
            try: extract_text(store,session.get(u,timeout=25,headers={'User-Agent':UA,'Accept-Language':'ko-KR,ko;q=0.9'}).text,u)
            except Exception: pass

def mutate_pages(obj,page,size=20):
    found=False
    def rec(x):
        nonlocal found
        if isinstance(x,dict):
            out={}
            for k,v in x.items():
                if k in PAGE_KEYS: out[k]=page; found=True
                elif k in OFFSET_KEYS: out[k]=(page-1)*size; found=True
                elif k in SIZE_KEYS and isinstance(v,(int,float,str)): out[k]=size
                else: out[k]=rec(v)
            return out
        if isinstance(x,list): return [rec(v) for v in x]
        return x
    return rec(copy.deepcopy(obj)),found

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--shard',type=int,required=True); ap.add_argument('--shards',type=int,default=8); ap.add_argument('--out',type=Path,required=True); a=ap.parse_args()
    store={}; events=[]; captured=[]; listing_requests=[]; session=requests.Session()
    selected=[t for i,t in enumerate(TERMS) if i%a.shards==a.shard]
    for t in selected:http_search(store,session,t)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,args=['--no-sandbox','--disable-dev-shm-usage','--disable-blink-features=AutomationControlled'])
        ctx=browser.new_context(locale='ko-KR',timezone_id='Asia/Seoul',user_agent=UA,viewport={'width':1440,'height':1200})
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page=ctx.new_page()
        def on_response(resp):
            u=resp.url
            if '/api/v1/listing' in u:
                req=resp.request
                try: body=resp.text()
                except Exception: body=''
                before=len(store); extract_text(store,body,u)
                listing_requests.append({'url':u,'method':req.method,'postData':req.post_data,'headers':{k:v for k,v in req.headers.items() if k.lower() in {'content-type','accept','referer','origin'}},'status':resp.status,'added':len(store)-before})
            elif any(k in u.lower() for k in ['product','vendor','seller','shop','search','category','page','list']):
                try:
                    body=resp.text(); before=len(store); extract_text(store,body,u)
                    if len(store)>before or 'api' in u.lower(): captured.append({'url':u,'status':resp.status,'added':len(store)-before})
                except Exception: pass
        page.on('response',on_response)
        targets=[f'{SHOP}/{SELLER_ID}?locale=ko_KR&platform=p',f'{SHOP}/{SELLER_ID}?locale=ko_KR&platform=m']
        for target in targets:
            try:
                page.goto(target,wait_until='networkidle',timeout=90000); page.wait_for_timeout(2000)
                for _ in range(60):
                    page.mouse.wheel(0,2600); page.wait_for_timeout(250)
                extract_text(store,page.content(),target)
                events.append({'target':target,'title':page.title(),'count':len(store)})
            except Exception as e: events.append({'target':target,'error':f'{type(e).__name__}: {e}','count':len(store)})
        # Replay the exact successful listing request while changing only pagination fields.
        seen_req=set()
        for lr in list(listing_requests):
            if lr['status']!=200: continue
            sig=(lr['method'],lr.get('postData') or '')
            if sig in seen_req: continue
            seen_req.add(sig)
            raw=lr.get('postData') or ''
            try: payload=json.loads(raw) if raw else None
            except Exception: payload=None
            for pg in range(1,31):
                try:
                    kwargs={'method':lr['method'],'headers':lr['headers'],'timeout':30000}
                    if payload is not None:
                        p2,found=mutate_pages(payload,pg,20)
                        if not found:
                            if isinstance(p2,dict): p2.update({'page':pg,'pageSize':20,'storeId':STORE_ID,'vendorId':SELLER_ID})
                        kwargs['data']=json.dumps(p2,ensure_ascii=False)
                    elif raw: kwargs['data']=raw
                    r=ctx.request.fetch(lr['url'],**kwargs); txt=r.text(); before=len(store); extract_text(store,txt,f'{lr["url"]}#replay-{pg}')
                    captured.append({'url':lr['url'],'status':r.status,'page':pg,'added':len(store)-before})
                    if pg>=3 and len(store)-before==0 and r.status!=200: break
                except Exception as e: captured.append({'url':lr['url'],'page':pg,'error':f'{type(e).__name__}: {e}'})
        for term in selected:
            for pg in range(1,16):
                for u in [f'{BASE}/np/search?q={quote(term)}&channel=user&page={pg}',f'https://m.coupang.com/nm/search?q={quote(term)}&page={pg}']:
                    try: page.goto(u,wait_until='domcontentloaded',timeout=35000); page.wait_for_timeout(600); extract_text(store,page.content(),u)
                    except Exception: pass
        browser.close()
    rows=sorted(store.values(),key=lambda x:int(x['productId']))
    payload={'seller':SELLER,'sellerId':SELLER_ID,'storeId':STORE_ID,'shard':a.shard,'shards':a.shards,'count':len(rows),'terms':selected,'events':events,'listingRequests':listing_requests,'capturedResponses':captured[-1000:],'products':rows,'generatedAt':now()}
    a.out.parent.mkdir(parents=True,exist_ok=True); a.out.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    print({'shard':a.shard,'count':len(rows),'listingRequests':len(listing_requests)},flush=True)
if __name__=='__main__': main()
