#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests

SCRIPT = "https://front.coupangcdn.com/coupang-store-display/20260324160003_kr/f6ae536.js"
ENDPOINTS = (
    "/api/v2/store/individualInfo/product",
    "/api/v2/store/individualInfo/products",
)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.7",
    "Referer": "https://shop.coupang.com/A00214628",
}


def save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def snippets(text: str, needle: str, before: int = 5000, after: int = 10000, limit: int = 20) -> list[str]:
    out: list[str] = []
    for match in re.finditer(re.escape(needle), text):
        out.append(text[max(0, match.start() - before): min(len(text), match.end() + after)])
        if len(out) >= limit:
            break
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    session = requests.Session()
    session.headers.update(HEADERS)
    result: dict[str, Any] = {"script": SCRIPT, "maps": [], "endpointContexts": {}, "assignmentContexts": []}

    response = session.get(SCRIPT, timeout=60)
    text = response.text
    result["scriptStatus"] = response.status_code
    result["scriptLength"] = len(response.content)
    result["scriptContentType"] = response.headers.get("content-type", "")
    result["sourceMappingComments"] = re.findall(r"sourceMappingURL=([^\s*]+)", text)[-10:]

    for endpoint in ENDPOINTS:
        result["endpointContexts"][endpoint] = snippets(text, endpoint)

    endpoint_positions = [match.start() for endpoint in ENDPOINTS for match in re.finditer(re.escape(endpoint), text)]
    for position in endpoint_positions:
        window = text[max(0, position - 120000):position]
        candidates = list(re.finditer(r"(?:^|[,;])\s*([A-Za-z_$][\w$]*)\s*=", window))
        for match in candidates[-80:]:
            name = match.group(1)
            if name != "S":
                continue
            absolute = max(0, position - 120000) + match.start()
            result["assignmentContexts"].append({
                "name": name,
                "distanceToEndpoint": position - absolute,
                "context": text[max(0, absolute - 3000): min(len(text), absolute + 12000)],
            })

    map_urls: list[str] = []
    for value in result["sourceMappingComments"]:
        map_urls.append(urljoin(SCRIPT, value.strip('"\'')))
    map_urls.extend([SCRIPT + ".map", re.sub(r"\.js$", ".js.map", SCRIPT)])
    seen: set[str] = set()

    for url in map_urls:
        if url in seen:
            continue
        seen.add(url)
        row: dict[str, Any] = {"url": url}
        try:
            map_response = session.get(url, timeout=90)
            row.update({
                "status": map_response.status_code,
                "length": len(map_response.content),
                "contentType": map_response.headers.get("content-type", ""),
                "prefix": map_response.text[:1000],
            })
            if map_response.ok:
                try:
                    source_map = map_response.json()
                except Exception as exc:
                    row["jsonError"] = f"{type(exc).__name__}: {exc}"
                else:
                    sources = source_map.get("sources") or []
                    contents = source_map.get("sourcesContent") or []
                    row["sourceCount"] = len(sources)
                    row["matchingSources"] = []
                    for index, source in enumerate(sources):
                        content = contents[index] if index < len(contents) and contents[index] else ""
                        if any(endpoint in content for endpoint in ENDPOINTS):
                            row["matchingSources"].append({
                                "source": source,
                                "contexts": {
                                    endpoint: snippets(content, endpoint, before=5000, after=12000, limit=10)
                                    for endpoint in ENDPOINTS
                                    if endpoint in content
                                },
                            })
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
        result["maps"].append(row)

    result["summary"] = {
        "scriptStatus": result.get("scriptStatus"),
        "sourceMapCandidates": len(result["maps"]),
        "usableSourceMaps": sum(1 for row in result["maps"] if row.get("sourceCount")),
        "matchingSourceFiles": sum(len(row.get("matchingSources", [])) for row in result["maps"]),
        "sAssignmentCandidates": len(result["assignmentContexts"]),
    }
    save(args.output, result)
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
