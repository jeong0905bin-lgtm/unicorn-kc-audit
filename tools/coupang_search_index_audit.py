#!/usr/bin/env python3
import json, random, re, time, urllib.parse
from pathlib import Path
import requests
from bs4 import BeautifulSoup

LISTING='https://shop.coupang.com/api/v1/listing'
OUT=Path('diagnostics/search-index/result.json')
UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/150 Safari/537.36'
KC_RE=re.compile(r'\b(?:CB|YU|SU|HU|CA|XU|B|A|R|U)[0-9A-Z-]{6,}\b',re.I)
PUB_RE=re.compile(r'저자\s*[,/]?\s*출판사\s*[:：]?\s*유니콘')

def plain(s):
    return re.sub(r'\s+',' ',BeautifulSoup(s or '','html.parser').get_text(' ',strip=True))

def candidates():
    rows=[]
    page=0
    with requests.Session() as s:
        while True:
            payload={'sellerId':'A00214628','storeId':79545,'outboundShippingPlaceId':1208642,'page':page,'size':20,'query':'유니콘'}
            r=s.post(LISTING,json=payload,headers={'User-Agent':UA,'Referer':'https://shop.coupang.com/A00214628'},timeout=30)
            r.raise_for_status(); data=r.json()
            body=data.get('data') or data
            items=body.get('products') or body.get('content') or body.get('items') or []
            for x in items:
                pid=str(x.get('productId') or '')
                iid=str(x.get('itemId') or '')
                vid=str(x.get('vendorItemId') or '')
                name=x.get('productName') or x.get('name') or x.get('title') or ''
                if pid and iid:
                    rows.append({'productId':pid,'itemId':iid,'vendorItemId':vid,'sourceName':name})
            total=int(body.get('total') or body.get('totalElements') or len(rows))
            if not items or len(rows)>=total: break
            page+=1
    uniq={(x['productId'],x['itemId']):x for x in rows}
    return list(uniq.values())

def search_html(session,q):
    engines=[('bing','https://www.bing.com/search?q='),('duck','https://html.duckduckgo.com/html/?q=')]
    attempts=[]
    for name,base in engines:
        try:
            r=session.get(base+urllib.parse.quote_plus(q),headers={'User-Agent':UA,'Accept-Language':'ko-KR,ko;q=0.9'},timeout=25)
            attempts.append({'engine':name,'status':r.status_code,'length':len(r.text)})
            if r.status_code==200 and len(r.text)>500: return plain(r.text),attempts
        except Exception as e: attempts.append({'engine':name,'error':str(e)})
    return '',attempts

def main():
    OUT.parent.mkdir(parents=True,exist_ok=True)
    products=candidates(); results=[]
    session=requests.Session()
    for i,p in enumerate(products,1):
        pid=p['productId']; title=p['sourceName']
        queries=[f'site:coupang.com/vp/products/{pid} "저자, 출판사" "유니콘"',f'"{pid}" "{title}" "유니콘"',f'"{pid}" "KC" "유니콘"']
        texts=[]; attempts=[]
        for q in queries:
            txt,att=search_html(session,q); texts.append(txt); attempts.extend([{'query':q,**a} for a in att])
            time.sleep(random.uniform(1.2,2.2))
        evidence=' '.join(texts)
        exact=bool(PUB_RE.search(evidence))
        kcs=sorted({m.group(0).upper() for m in KC_RE.finditer(evidence) if m.group(0).upper()!='U003E1577-7011'})
        snippets=[]
        for needle in ('저자, 출판사','저자 출판사','유니콘','KC'):
            pos=evidence.find(needle)
            if pos>=0: snippets.append(evidence[max(0,pos-180):pos+420])
        results.append({**p,'publisherExactUnicornFromIndex':exact,'kcCandidates':kcs,'evidenceSnippets':snippets[:8],'attempts':attempts})
        summary={'candidateTotal':len(products),'processed':len(results),'exactUnicorn':sum(r['publisherExactUnicornFromIndex'] for r in results),'withKC':sum(bool(r['kcCandidates']) for r in results),'results':results}
        OUT.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'i':i,'productId':pid,'exact':exact,'kcs':kcs},ensure_ascii=False),flush=True)
    print(OUT.read_text(encoding='utf-8'))

if __name__=='__main__': main()
