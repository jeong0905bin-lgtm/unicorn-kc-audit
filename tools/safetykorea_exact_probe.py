import json
import os
import re
from pathlib import Path

import requests
from bs4 import BeautifulSoup

DEFAULT_CERTS = [
    "CB064H009-2001",
    "CB064H009-3002",
    "CB064H009-3003",
    "CB064H009-4001",
    "CB064H009-4002",
    "CB064H009-4003",
    "CB064H009-8001",
    "CB064H009-9001",
    "CB064H009-9002",
    "CB064H009-9003",
]

STATUS_VALUES = ["기간만료", "적합", "취소", "반납", "정지", "부적합"]
CERT_PATTERN = re.compile(r"\b[A-Z]{2}\d{3}[A-Z]\d{3}-\d{4}\b", re.I)
EXCLUDED = {"U003E1577-7011"}


def clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def discover_certs():
    certs = set(DEFAULT_CERTS)
    env = os.environ.get("SAFETYKOREA_CERTS", "")
    certs.update(CERT_PATTERN.findall(env))
    for path in Path("diagnostics").rglob("*.json"):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        certs.update(CERT_PATTERN.findall(text))
    return sorted(c.upper() for c in certs if c.upper() not in EXCLUDED)


def extract(cert, html):
    soup = BeautifulSoup(html, "html.parser")
    text = clean(soup.get_text(" "))
    status = ""

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
    certs = discover_certs()
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    })
    rows = []
    for cert in certs:
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

    resolved = [r for r in rows if r.get("exactNumberPresent") and r.get("status")]
    unresolved = [r["certificationNumber"] for r in rows if not (r.get("exactNumberPresent") and r.get("status"))]
    summary = {
        "requestedCount": len(certs),
        "results": rows,
        "exactResolved": len(resolved),
        "unresolved": unresolved,
        "expired": [r["certificationNumber"] for r in rows if r.get("expired")],
        "complete": not unresolved,
        "excludedCertificationNumbers": sorted(EXCLUDED),
    }
    output = Path(os.environ.get("PROBE_OUTPUT", "diagnostics/safetykorea/result.json"))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
