# Unicorn KC Audit — Persistent Project State

Updated: 2026-08-01 01:46 KST

## Objective

Audit every current product from Coupang seller `A00214628`.

Final publisher inclusion rule:

- Read product-level publisher evidence.
- Accept only a field labeled `출판사` or an exact mandatory-disclosure row equivalent.
- Normalize whitespace and punctuation.
- Include only when the value is exactly `유니콘`.
- Never infer publisher from title, brand, product family, or seller.

For accepted products:

1. Preserve the current product name.
2. Preserve Coupang unique ID as `productId - itemId`.
3. Collect KC numbers only from exact-product evidence.
4. Mark `없음` only after the product was actually processed and no KC was found.
5. Keep inaccessible and manual-review cases explicit.
6. Validate every accepted unique KC number with official SafetyKorea.
7. Put a row in the expired workbook only when both the product-to-KC mapping and official `기간만료` status are confirmed.
8. Never include `U003E1577-7011`.

## Current catalog

- Seller listing API: `/api/v1/listing`
- Current reported/collected catalog: **2,229 / 2,229**
- Previous 2,230 count is stale and superseded.
- Previous 120-product result and old blank workbooks remain permanently discarded.

## Publisher classification

### Singular detail API

Latest merged run: `30644484268`

- Successfully classified: **1,825**
- Unresolved: **404**
- Coverage: **81.8753%**
- Exact `출판사 = 유니콘` from API attributes: **30**

Unresolved response classes:

- HTTP 403, no data: 218
- HTTP 200, empty/no usable data: 176
- HTTP 400, no data: 10

The plural-link metadata retry added 2 accessible non-Unicorn products and no new exact Unicorn products.

### Additional exact publisher evidence

Three catalog products are accepted from exact evidence outside the successful API set:

- `1321324685 - 2342362756` — 유니콘 신기한 워터 색칠북 - 콩순이
- `6732125611 - 14240107530` — 신세계 인지향상 고도리 퍼즐 3종 세트
- `8411161016 - 24319968314` — (BOOKFRIENDS) 위시캣 스티커퀸 300

### Consolidated accepted scope

- API exact products: **30**
- Additional exact products: **3**
- Consolidated exact publisher products: **33**

Canonical files:

- `diagnostics/consolidated-unicorn-products.json`
- `diagnostics/kc-mapping-addendum-20260801.json`

## Plural endpoint result

Run `30643392302` scanned `/api/v2/store/individualInfo/products`.

- Usable metadata response: **2,076 / 2,229**
- Unresolved: **153**
- Endpoint coverage: **93.1359%**
- Publisher attributes exposed: **0**

This endpoint is retained for product metadata and link recovery only. It cannot classify publisher.

## KC evidence

Exact product-to-KC mappings are currently confirmed for **10 products**.

Confirmed unique KC numbers:

- `CB064H009-2001`
- `CB064H009-3002`
- `CB064H009-3003`
- `CB064H009-4001`
- `CB064H009-4003`
- `CB064H009-8001`
- `CB064H009-9002`

Newest exact mappings:

- `1318402202` 엉덩이탐정 스티커 컬렉션북 → `CB064H009-9002`
- `8616958567` 슈팅스타 캐치티니핑 신기한 워터색칠북 2권 → ISBN `8806328724817` → `CB064H009-3003`
- `7201864624` 슈퍼다이노 신기한 워터색칠북 → ISBN `8806328723063` → `CB064H009-8001`

Official SafetyKorea status resolved so far:

- `CB064H009-3002` — **적합**, manufacturer `주식회사 유니콘`, model `스티커 컬렉션`
- `CB064H009-9003` — **기간만료**, manufacturer `주식회사 유니콘`, model `캐릭터 퍼즐`

Important: `CB064H009-9003` is not yet mapped to any exact current catalog product. Therefore it must not produce an expired-workbook row yet.

Official status remains unresolved for:

- `CB064H009-2001`
- `CB064H009-3003`
- `CB064H009-4001`
- `CB064H009-4003`
- `CB064H009-8001`
- `CB064H009-9002`

## Required outputs

### Workbook 1 — No KC

Columns:

- 상품명
- 쿠팡 상품 고유번호
- KC 인증번호

Use `없음` only for products whose complete detail/KC evidence was processed successfully.

### Workbook 2 — Expired KC

Columns:

- 기한만료 KC 인증번호
- 만료된 상품명
- 쿠팡 상품 고유번호

Rules:

- One row per product/KC pair.
- A duplicated KC used by multiple products gets one row per product.
- Active or unresolved certification status is excluded.

## Current blockers

- Publisher classification remains unresolved for 404 catalog products.
- 23 accepted Unicorn products still lack exact product-level KC evidence.
- Six accepted KC numbers still need official SafetyKorea status resolution.
- No current catalog product is yet exactly mapped to the known expired code `CB064H009-9003`.
- No-KC conclusions are not yet complete enough for a final workbook.

## Execution policy

- Do not repeat broad API retries that have already yielded negligible recovery.
- Use exact-product identifiers, ISBN/GTIN, mandatory disclosure, retailer records, archived exact pages, and user HAR evidence.
- Keep exact confirmation separate from product-family similarity.
- Do not promote a KC candidate until exact-product identity is established.
- Do not create or deliver final workbooks until validation gates pass.

## Immediate next work

1. Resolve exact KC mappings for the remaining 23 accepted publisher products.
2. Resolve official SafetyKorea status for `2001`, `3003`, `4001`, `4003`, `8001`, and `9002`.
3. Search for exact current-catalog use of expired `9003`.
4. Continue publisher recovery only through materially different evidence routes.
5. Generate final workbooks only after the mapping and official-status gates are complete.
