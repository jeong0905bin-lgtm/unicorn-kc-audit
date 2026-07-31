#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import requests

BASE = "https://shop.coupang.com"
SCRIPT = "https://front.coupangcdn.com/coupang-store-display/20260324160003_kr/f6ae536.js"
SELLER_ID = "A00214628"
STORE_ID = 79545
KNOWN = {
    "productId": "8411161016",
    "itemId": "24319968314",
    "vendorItemId": "91335726263",
}
STORE_URL = f"{BASE}/{SELLER_ID}"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.7",
    "Content-Type": "application/json",
    "Origin": BASE,
    "Referer": STORE_URL,
}
ROUTES = [
    "/api/v2/store/individualInfo/product",
    "/api/v2/store/individualInfo/products",
]


def save(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def hits(value, path="$", out=None):
    if out is None:
        out=[]
    if isinstance(value, dict):
        for k,v in value.items():
            hits(v,f"{path}.{k}",out)
    elif isinstance(value,list):
        for i,v in enumerate(value):
            hits(v,f"{path}[{i}]",out)
    else:
        text=str(value or "")
        if any(t.lower() in text.lower() for t in ("유니콘","저자","출판사","kc","cert","8411161016","24319968314","91335726263")):
            out.append({"path":path,"value":text[:1000]})
    return out


def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()

    session=requests.Session()
    session.headers.update(HEADERS)
    result={"script":SCRIPT,"contexts":{},"attempts":[]}
    js=session.get(SCRIPT,timeout=40)
    result["scriptStatus"]=js.status_code
    result["scriptLength"]=len(js.content)
    text=js.text
    for route in ROUTES:
        positions=[m.start() for m in re.finditer(re.escape(route),text)]
        result["contexts"][route]=[
            text[max(0,p-2500):min(len(text),p+5000)] for p in positions[:5]
        ]

    payloads=[
        {"vendorId":SELLER_ID,"storeId":STORE_ID,**KNOWN},
        {"vendorId":SELLER_ID,"storeId":STORE_ID,"productIds":[KNOWN["productId"]]},
        {"vendorId":SELLER_ID,"productId":KNOWN["productId"]},
        {"storeId":STORE_ID,"productId":KNOWN["productId"],"vendorItemId":KNOWN["vendorItemId"]},
        {"vendorId":SELLER_ID,"vendorItemId":KNOWN["vendorItemId"]},
    ]
    for route in ROUTES:
        url=BASE+route
        for method in ("GET","POST"):
            for payload in payloads:
                row={"route":route,"method":method,"payload":payload}
                try:
                    if method=="GET":
                        response=session.get(url,params=payload,timeout=30)
                    else:
                        response=session.post(url,json=payload,timeout=30)
                    row.update({"status":response.status_code,"contentType":response.headers.get("content-type",""),"length":len(response.content),"url":response.url,"bodyPrefix":response.text[:500]})
                    if response.ok:
                        try:
                            data=response.json()
                        except Exception as exc:
                            row["jsonError"]=f"{type(exc).__name__}: {exc}"
                        else:
                            row["jsonHits"]=hits(data)[:300]
                            row["topLevelKeys"]=sorted(data.keys()) if isinstance(data,dict) else []
                            row["jsonPreview"]=data
                except Exception as exc:
                    row["error"]=f"{type(exc).__name__}: {exc}"
                result["attempts"].append(row)

    save(args.output,result)
    print(json.dumps({"scriptStatus":result["scriptStatus"],"contextCounts":{k:len(v) for k,v in result["contexts"].items()},"attempts":[{k:r.get(k) for k in ("route","method","status","length") } for r in result["attempts"]]},ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
