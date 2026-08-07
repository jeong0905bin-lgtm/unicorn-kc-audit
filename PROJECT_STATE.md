# Unicorn KC Audit — Persistent Project State

Updated: 2026-08-02 22:59 KST

## Objective and fixed rules

Audit the current Coupang seller catalog for `A00214628` and produce two validated XLSX files: No-KC and expired-KC.

- Current catalog count: **2,229**. The former 2,230 count is stale.
- Include products when publisher `유니콘` is verified by exact Coupang detail attributes, exact mandatory disclosure, ISBN-linked official book information, or equivalent exact evidence.
- Do not infer from a character name or brand name alone.
- Blocked, empty, or unusable responses are unresolved, not complete.
- Product-to-KC mappings must be exact-product mappings.
- Validate every included KC number against official SafetyKorea.
- Always exclude `U003E1577-7011`.
- Do not merge PR #1.
- Do not repeat BTF probes, browser-extension bulk collection, broad retries with the same request shape, or Open API routes requiring keys.

## Latest publisher recovery

Latest merged singular-link retry run: **30741271264**.

- Catalog: **2,229**
- Successful detail payloads: **1,849**
- Unresolved: **380**
- Coverage: **82.9520%**
- Newly recovered in this run: **26**
- Exact `출판사 = 유니콘`: **37**
- Newly recovered exact-Unicorn products: **7**
- Retry attempts: **6,766**

Remaining unresolved response classes:

- HTTP 403, no usable data: **186**
- HTTP 200, empty/no usable data: **176**
- HTTP 400, no usable data: **18**

The 380 unresolved products remain open. These response classes are not completion evidence.

## Official SafetyKorea status

Latest exact status run: **30741271280**.

All **12 requested certification numbers** returned exact official records. The official-status phase is complete for this current registry.

### Period expired

- `CB064H009-8001`
- `CB064H009-9001`
- `CB064H009-9002`
- `CB064H009-9003`

### Active / compliant

- `CB064H009-2001`
- `CB064H009-3002`
- `CB064H009-3003`
- `CB064H009-4001`
- `CB064H009-4002`
- `CB064H009-4003`
- `CB064H208-3002`
- `CB064H284-2001`

The last two active codes belong to other manufacturers and must only be used when an exact product-level mapping supports them.

## Current output gate

The workbook builder can generate a verified snapshot, but the **full audit is not complete**.

Completion is blocked by evidence coverage, not by SafetyKorea status:

1. **380** current catalog products still lack a successful publisher-detail payload or equivalent exact external publisher resolution.
2. Accepted Unicorn products still need complete exact product-level KC/no-KC dispositions.
3. No-KC rows must not be inferred from missing evidence.
4. Expired rows require both exact product-to-KC mapping and one of the four official expired statuses above.

## Canonical evidence

- `diagnostics/consolidated-unicorn-products.json`
- `diagnostics/official-status-recovery-20260802-run1.json`
- `diagnostics/kc-mapping-addendum-20260801.json`
- `diagnostics/kc-mapping-addendum-20260801-run1.json`
- Workflow artifact `unicorn-singular-link-merged-30741271264`
- Workflow artifact `safetykorea-exact-status-30741271280`

## Next non-repeating work

1. Resolve the 380 publisher-unresolved products through ISBN/GTIN, exact official book records, archived exact product pages, and already-collected HAR/link metadata—not another identical broad retry.
2. Consolidate the 37 exact API publisher matches with exact external publisher matches and deduplicate by `productId-itemId`.
3. Resolve each accepted product to exact KC or verified No-KC evidence.
4. Bind exact product mappings to the four expired official codes.
5. Generate and deliver the two XLSX files only when all 2,229 products have a terminal publisher disposition and every accepted Unicorn product has a terminal KC disposition.
