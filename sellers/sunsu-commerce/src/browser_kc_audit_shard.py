#!/usr/bin/env python3
from __future__ import annotations
import argparse, html, json, re, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus
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

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--input',type=Path,required=True); ap.add_argument('--out',type=Path,required=True); a=ap.parse_args()
    data=json.loads(a.input.read_text(encoding='utf-8')); rows=data.get('products',[]); logs=[]; output=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,args=['--disable-blink-features=AutomationControlled','--no-sandbox'])
        context=browser.new_context(locale='ko-KR',timezone_id='Asia/Seoul',user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36')
        context.set_default_timeout(12000)
        page=context.new_page()
        for index,row0 in enumerate(rows):
            row=dict(row0); unicorn=is_unicorn(row); row['isUnicorn']=unicorn
            row['publisherGrade']='확정' if unicorn else '제외'; row['publisherReason']='쿠팡 수집 필드 직접 표기' if unicorn else ''
            if not unicorn: output.append(row); continue
            pid=str(row.get('productId') or ''); item=str(row.get('itemId') or ''); vendor=str(row.get('vendorItemId') or '')
            urls=[]
            if row.get('productUrl'): urls.append(row['productUrl'])
            if pid: urls += [f'https://www.coupang.com/vp/products/{pid}?itemId={item}&vendorItemId={vendor}',f'https://m.coupang.com/vm/products/{pid}?itemId={item}&vendorItemId={vendor}']
            found=[]; source=''
            for url in dict.fromkeys(urls):
                try:
                    response=page.goto(url,wait_until='domcontentloaded',timeout=20000)
                    page.wait_for_timeout(1800)
                    body=page.content()+' '+page.locator('body').inner_text(timeout=5000)
                    cs=candidates(body)
                    status=response.status if response else 0
                    logs.append({'productId':pid,'stage':'browser-coupang','url':url,'http':status,'found':cs,'checkedAt':now()})
                    if cs: found=cs; source=url; break
                except PlaywrightTimeout:
                    logs.append({'productId':pid,'stage':'browser-coupang','url':url,'state':'timeout','checkedAt':now()})
                except Exception as e:
                    logs.append({'productId':pid,'stage':'browser-coupang','url':url,'state':type(e).__name__,'checkedAt':now()})
                time.sleep(1.2)
            row['kcCandidates']=found; row['kcNumber']=found[0] if found else ''; row['kcSource']=source if found else '브라우저 렌더링에서도 exact 표기 없음'; row['kcStatus']='후보 발견-공식 검증 필요' if found else 'KC 공개표기 없음'
            output.append(row)
        context.close(); browser.close()
    payload={'shard':data.get('shard'),'inputCount':len(rows),'unicornCount':sum(bool(r.get('isUnicorn')) for r in output),'kcFound':sum(bool(r.get('kcNumber')) for r in output),'products':output,'logs':logs,'generatedAt':now()}
    a.out.parent.mkdir(parents=True,exist_ok=True); a.out.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8'); print({k:payload[k] for k in ('shard','inputCount','unicornCount','kcFound')})
if __name__=='__main__': main()
