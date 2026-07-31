#!/usr/bin/env python3
from __future__ import annotations
import argparse,json
from pathlib import Path

def load(p): return json.loads(Path(p).read_text(encoding='utf-8'))
def save(p,v):
 p=Path(p); p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding='utf-8')

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--base',type=Path,required=True); ap.add_argument('--shards',type=Path,required=True); ap.add_argument('--output',type=Path,required=True); a=ap.parse_args()
 base=load(a.base); old=list(base.get('products') or [])
 recovered=[]
 for p in sorted(a.shards.rglob('*.json')):
  d=load(p); recovered.extend(d.get('results') or [])
 by_vi={str(r.get('catalogVendorItemId') or ''):r for r in recovered if r.get('recovered')}
 unresolved=[]; exact=[]; successful=[]
 for row in old:
  vi=str(row.get('catalogVendorItemId') or '')
  new=by_vi.get(vi)
  if new:
   successful.append(new)
   if new.get('publisherExactUnicorn'): exact.append(new)
  else: unresolved.append(row)
 summary={
  'inputUnresolved':len(old),'recoveredCount':len(successful),'remainingUnresolved':len(unresolved),
  'newExactUnicornCount':len(exact),'recoveryRatio':len(successful)/len(old) if old else 1,
 }
 save(a.output/'summary.json',summary); save(a.output/'recovered-products.json',{'count':len(successful),'products':successful}); save(a.output/'remaining-unresolved.json',{'count':len(unresolved),'products':unresolved}); save(a.output/'new-exact-unicorn-products.json',{'count':len(exact),'products':exact})
 print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
