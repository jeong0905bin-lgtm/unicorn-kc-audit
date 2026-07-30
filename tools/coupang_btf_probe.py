import json
import os
from pathlib import Path

import requests

CASES = [
    {
        "id": "known-unicorn",
        "productId": "9237088992",
        "itemId": "24365968915",
        "vendorItemId": "94313393561",
        "expectedPublisher": "유니콘",
    },
    {
        "id": "har-confirmed-unicorn",
        "productId": "8411161016",
        "itemId": "24319968314",
        "vendorItemId": "91335726263",
        "expectedPublisher": "유니콘",
    },
]


def normalize(value):
    return "".join(str(value or "").split()).strip(" ,/·|")


def parse_payload(data):
    essentials = data.get("essentials") or []
    publisher = ""
    for row in essentials:
        title = normalize(row.get("title"))
        if title == normalize("저자, 출판사"):
            publisher = normalize(row.get("description"))
            break
    certifications = data.get("certifications") or []
    kc = []
    for cert in certifications:
        number = normalize(cert.get("certificationNo"))
        if number and number not in kc:
            kc.append(number)
    return {
        "publisher": publisher,
        "publisherExactUnicorn": publisher == "유니콘",
        "kcNumbers": kc,
        "essentialCount": len(essentials),
        "certificationCount": len(certifications),
        "detailSectionCount": len(data.get("details") or []),
    }


def run_case(case):
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": f"https://www.coupang.com/vp/products/{case['productId']}?itemId={case['itemId']}&vendorItemId={case['vendorItemId']}",
        "X-Requested-With": "XMLHttpRequest",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    })
    endpoint = (
        "https://www.coupang.com/next-api/products/btf"
        f"?productId={case['productId']}"
        f"&vendorItemId={case['vendorItemId']}"
        f"&itemId={case['itemId']}"
    )
    result = {"case": case, "endpoint": endpoint}
    try:
        response = session.get(endpoint, timeout=30)
        result.update({
            "statusCode": response.status_code,
            "contentType": response.headers.get("content-type", ""),
            "bodyLength": len(response.content),
            "bodyPrefix": response.text[:300],
        })
        if response.ok:
            try:
                data = response.json()
                result["parsed"] = parse_payload(data)
            except Exception as exc:
                result["jsonError"] = repr(exc)
    except Exception as exc:
        result["requestError"] = repr(exc)
    return result


def main():
    output = Path(os.environ.get("PROBE_OUTPUT", "diagnostics/btf-probe/result.json"))
    output.parent.mkdir(parents=True, exist_ok=True)
    results = [run_case(case) for case in CASES]
    summary = {
        "runner": os.environ.get("RUNNER_LABEL", "unknown"),
        "results": results,
        "usable": any((r.get("parsed") or {}).get("publisherExactUnicorn") for r in results),
    }
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
