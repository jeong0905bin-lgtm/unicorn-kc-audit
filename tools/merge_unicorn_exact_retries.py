#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def product_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row.get("catalogIndex") or ""),
        str(row.get("catalogProductId") or row.get("productId") or ""),
        str(row.get("catalogItemId") or row.get("itemId") or ""),
        str(row.get("catalogVendorItemId") or row.get("vendorItemId") or row.get("requestedVendorItemId") or ""),
    )


def base_file(base: Path, name: str) -> Path:
    direct = base / name
    nested = base / "exact" / name
    if direct.exists():
        return direct
    if nested.exists():
        return nested
    raise FileNotFoundError(f"base file not found: {name}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--shards", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-run-id", type=int, default=0)
    args = parser.parse_args()

    base_summary = load(base_file(args.base, "summary.json"))
    base_exact = load(base_file(args.base, "exact-unicorn-products.json"))
    base_unresolved = load(base_file(args.base, "unresolved-products.json"))
    shard_paths = sorted(args.shards.glob("**/result.json"))
    if not shard_paths:
        raise SystemExit("no shard result files found")

    shard_docs = [load(path) for path in shard_paths]
    expected_shards = {int(doc["shardIndex"]) for doc in shard_docs}
    shard_count = max(int(doc["shardCount"]) for doc in shard_docs)
    if expected_shards != set(range(shard_count)):
        raise SystemExit(f"incomplete shards: found {sorted(expected_shards)}, expected 0..{shard_count - 1}")

    recovered: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    unresolved: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    attempts: list[dict[str, Any]] = []
    for doc in shard_docs:
        for row in doc.get("recovered") or []:
            recovered[product_key(row)] = row
        for row in doc.get("unresolved") or []:
            unresolved[product_key(row)] = row
        attempts.extend(doc.get("attempts") or [])

    base_unresolved_count = int(base_unresolved.get("count") or 0)
    if len(recovered) + len(unresolved) != base_unresolved_count:
        raise SystemExit(
            f"retry coverage mismatch: recovered {len(recovered)} + unresolved {len(unresolved)} != {base_unresolved_count}"
        )

    exact: dict[tuple[str, str, str, str], dict[str, Any]] = {
        product_key(row): row for row in base_exact.get("products") or []
    }
    for row in recovered.values():
        if row.get("publisherExactUnicorn"):
            exact[product_key(row)] = row

    base_success = int(base_summary.get("successfulCount") or 0)
    final_success = base_success + len(recovered)
    catalog_count = int(base_summary.get("catalogCount") or 0)
    final_unresolved = catalog_count - final_success
    if final_unresolved != len(unresolved):
        raise SystemExit(f"final unresolved mismatch: computed {final_unresolved}, rows {len(unresolved)}")

    response_types: dict[str, int] = {}
    for row in unresolved.values():
        status = row.get("httpStatus")
        api_code = row.get("apiCode")
        data_present = row.get("dataPresent")
        key = f"http={status};api={api_code};data={data_present}"
        response_types[key] = response_types.get(key, 0) + 1

    history = list(base_summary.get("retryHistory") or [])
    history.append({
        "baseRunId": args.base_run_id or base_summary.get("baseRunId"),
        "baseSuccessfulCount": base_success,
        "baseUnresolvedCount": base_unresolved_count,
        "retryShardCount": shard_count,
        "retryRecoveredCount": len(recovered),
        "retryAttemptCount": len(attempts),
    })

    summary = {
        "sellerId": base_summary.get("sellerId"),
        "storeId": base_summary.get("storeId"),
        "catalogCount": catalog_count,
        "baseRunId": args.base_run_id or base_summary.get("baseRunId"),
        "baseSuccessfulCount": base_success,
        "baseUnresolvedCount": base_unresolved_count,
        "retryShardCount": shard_count,
        "retryRecoveredCount": len(recovered),
        "successfulCount": final_success,
        "unresolvedCount": len(unresolved),
        "completionRatio": final_success / catalog_count if catalog_count else 0,
        "baseExactUnicornCount": int(base_exact.get("count") or 0),
        "newExactUnicornCount": len(exact) - int(base_exact.get("count") or 0),
        "publisherExactUnicornCount": len(exact),
        "unresolvedResponseTypes": response_types,
        "retryAttemptCount": len(attempts),
        "retryHistory": history,
    }

    args.output.mkdir(parents=True, exist_ok=True)
    save(args.output / "summary.json", summary)
    save(args.output / "exact-unicorn-products.json", {
        "criterion": base_exact.get("criterion"),
        "count": len(exact),
        "products": sorted(exact.values(), key=lambda row: int(row.get("catalogIndex") or 0)),
    })
    save(args.output / "recovered-detail-results.json", {
        "count": len(recovered),
        "products": sorted(recovered.values(), key=lambda row: int(row.get("catalogIndex") or 0)),
    })
    save(args.output / "unresolved-products.json", {
        "count": len(unresolved),
        "products": sorted(unresolved.values(), key=lambda row: int(row.get("catalogIndex") or 0)),
    })
    save(args.output / "retry-attempts.json", attempts)
    save(args.output / "shard-summaries.json", [
        {key: doc.get(key) for key in (
            "shardIndex", "shardCount", "inputCount", "recoveredCount", "unresolvedCount",
            "newExactUnicornCount", "elapsedSeconds"
        )}
        for doc in sorted(shard_docs, key=lambda item: int(item["shardIndex"]))
    ])
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
