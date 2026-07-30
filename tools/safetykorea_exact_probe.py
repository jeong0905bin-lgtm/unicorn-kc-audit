import json
import os
import re
from pathlib import Path

import requests
from bs4 import BeautifulSoup

CERTS = [
    "CB064H009-2001",
    "CB064H009-3002",
    "CB064H009-4003",
    "CB064H009-8001",
]

STATUS_VALUES = ["기간만료", "적합", "취소", "반납", "정지", "부적합"]


def clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def extract(cert, html):
    soup = BeautifulSoup(html, "html.parser")
    text = clean(soup.get_text(" "))
    status = ""

    # Prefer table label/value parsing.
    for label in soup.find_all(string=lambda s: s and "인증상태" in s):
        cell = label.parent
        if cell:
            nxt = cell.find_next(["td", "dd"])
            if nxt:
                candidate = clean(nxt.get_text(" "))
                for value in STATUS_VALUES:
                    if value in candidate:
                        status = value
                        break
        if status:
            break

    if not status:
        m = re.search(r"인증상태\s*[:|]?\s*(기간만료|적합|취소|반납|정지|부적합)", text)
        if m:
            status = m.group(1)

    exact = cert.upper() in text.upper()
    return {
        "certificationNumber": cert,
        "exactNumberPresent": exact,
        "status": status,
        "expired": exact and status == "기간만료",
        "textPrefix": text[:500],
    }


def main():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    })
    rows = []
    for cert in CERTS:
        url = "https://www.safetykorea.kr/search/searchPop?certNum=" + cert
        row = {"certificationNumber": cert, "url": url}
        try:
            response = session.get(url, timeout=30)
            row.update({
                "statusCode": response.status_code,
                "contentType": response.headers.get("content-type", ""),
                "bodyLength": len(response.content),
            })
            if response.ok:
                row.update(extract(cert, response.text))
            else:
                row["bodyPrefix"] = response.text[:300]
        except Exception as exc:
            row["requestError"] = repr(exc)
        rows.append(row)

    summary = {
        "results": rows,
        "exactResolved": sum(bool(r.get("exactNumberPresent") and r.get("status")) for r in rows),
        "expired": [r["certificationNumber"] for r in rows if r.get("expired")],
    }
    output = Path(os.environ.get("PROBE_OUTPUT", "diagnostics/safetykorea/result.json"))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
