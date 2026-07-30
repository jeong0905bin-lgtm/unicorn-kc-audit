# Unicorn KC Audit — Persistent Project State

Updated: 2026-07-30 23:56 KST

## Objective

Audit all products from Coupang seller `A00214628`.

Final inclusion rule:

- Read the mandatory disclosure table on each product detail page.
- Use only the row labeled `저자, 출판사`.
- Normalize whitespace and punctuation.
- Include a product only when the value is exactly `유니콘`.
- Do not use product title, brand filter, seller filter, or guessed publisher as the final criterion.

For included products:

1. Preserve product name.
2. Preserve Coupang unique ID as `productId-itemId`.
3. Collect KC numbers from product detail content/images.
4. Mark truly processed products with no KC as `없음`.
5. Keep inaccessible, no-image, and manual-review cases separate.
6. Validate every accepted unique KC number through official SafetyKorea.
7. Include only official status `기간만료` in the expired workbook.
8. Never include `U003E1577-7011`.

## Required outputs

### Workbook 1 — No KC

Columns:

- 상품명
- 쿠팡 상품 고유번호
- KC 인증번호

Value for confirmed no-KC rows: `없음`

### Workbook 2 — Expired KC

Columns:

- 기한만료 KC 인증번호
- 만료된 상품명
- 쿠팡 상품 고유번호

Row rules:

- One product with multiple expired KCs: one row per KC.
- Same KC used by multiple products: one row per product.
- Active valid KC: exclude.

## Confirmed catalog state

- Seller listing API: `/api/v1/listing`
- Reported total: 2,230 products
- Collected unique products: 2,230
- Catalog coverage: complete
- Previous 120-product result: invalid and permanently discarded
- Old blank spreadsheets and old ZIP: never deliver

## Proven blocker

GitHub-hosted runners cannot access the actual Coupang product detail content needed for `저자, 출판사`.

Tested and failed:

- direct product URL
- seller-page-to-product same-session navigation
- six OS/browser combinations
- current itemId and older itemId variants
- brand-filter-only selection
- representative-image OCR
- search-engine indexed snippets
- Jina reader
- Common Crawl
- Wayback

Representative-image OCR is invalid for publisher classification because the seller API exposes cover thumbnails, not the mandatory disclosure table or full detail images.

## Current execution policy

All failed diagnostic workflows are manual-only. Do not re-enable automatic execution or repeat them.

Do not claim progress from queued jobs. Distinguish strictly:

- queued
- in progress
- completed successfully
- completed but produced no usable evidence

Do not report percentage progress unless tied to verified records.

## Only accepted next path

Use one logged-out Chrome HAR captured on an actual product detail page where the `필수 표기 정보` table is visible.

HAR capture must include:

1. Clear Network log.
2. Refresh product detail page.
3. Scroll to `필수 표기 정보`.
4. Export HAR with content.

From that HAR:

- identify the exact request/response containing `저자, 출판사`
- identify any detail/specification API
- identify detail image URLs used for KC extraction
- strip cookies, authorization, tracking IDs, and personal/session data
- never commit or publish raw HAR
- retain only sanitized request structure and non-sensitive payload fields

Before full execution, require a preflight that confirms a known product returns exactly `저자, 출판사 = 유니콘`.

Then run the sanitized request pattern across all 2,230 products with strict completeness gates.

## Non-negotiable safeguards

- Never guess publisher or KC number.
- Never use the 36-product brand-filter set as final scope.
- Never mark inaccessible products as `없음`.
- Never deliver blank workbooks as successful output.
- Never mix data from the previous seller/KC project.
- Fail the workflow when catalog processing is incomplete.
- Keep unresolved cases explicit.

## Immediate status

- Catalog: complete, 2,230/2,230
- Exact publisher classification: blocked pending one product-detail HAR
- KC extraction: not started for verified Unicorn products
- SafetyKorea validation: not started
- Final Excel files: not created
