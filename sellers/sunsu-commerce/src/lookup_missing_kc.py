#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, re
from pathlib import Path

KNOWN = [
"CB064H009-2001","CB064H009-3002","CB064H009-3003","CB064H009-4001",
"CB064H009-4002","CB064H009-4003","CB064H009-8001","CB064H009-9001",
"CB064H009-9002","CB064H009-9003"
]
EXCLUDED = {"U003E1577-7011"}

def norm(s: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", s or "").lower()

def main():
    p=argparse.ArgumentParser(); p.add_argument('--input',type=Path,required=True); p.add_argument('--evidence',type=Path,required=True); p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    rows=json.loads(a.input.read_text(encoding='utf-8'))
    evidence=json.loads(a.evidence.read_text(encoding='utf-8')) if a.evidence.exists() else []
    by_kc={str(x.get('kcNumber','')).upper():x for x in evidence}
    out=[]
    for row in rows:
        if row.get('kcNumber'): out.append(row); continue
        title=norm(row.get('productName',''))
        hit=None
        for kc in KNOWN:
            e=by_kc.get(kc,{})
            model=norm(e.get('modelName',''))
            if kc not in EXCLUDED and model and title and (title==model or title in model or model in title):
                hit=e; break
        if hit:
            row['kcNumber']=hit['kcNumber']; row['kcStatus']=hit.get('status','검증 불가'); row['kcMappingEvidence']='exact model/title match'
        else:
            row['kcStatus']='검증 불가' if row.get('responseState')!='ok' else 'KC 공개표기 없음'
            row['kcMappingEvidence']='no exact remembered mapping'
        out.append(row)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
if __name__=='__main__': main()
