# Changelog — Leads Bounty Product Research

## 1.2.0 — 2026-11-20
- **You choose every search.** `/leadsbounty scrap` asks only for the missing inputs (market or Worldwide, period, category,
  target), shows your selections, and runs only after you choose **Start search**. The CLI previews by default and needs `--start`
  to queue. There is no default market. Setup, status, reports and tests never start a paid search.
- **New period, Current snapshot.** Today's Best Sellers ranks count as Top Sellers. Newly Listed means first available within the
  last 30 days.
  - For a date range, current-only rankings are shown as additional discoveries and don't count toward the requested period.
- **The HTML dashboard is the main product.** `report.html` opens as the Leads Bounty product discovery dashboard:
  - Your market, period, category and target appear at the top.
  - Tabs: Requested results, All discoveries (the full deduplicated catalog, including candidates that were previously only in the
    exports) and Uncertain matches.
  - Search, sorting (price is grouped by currency and never converted), pagination, and filters for source, region and evidence
    type.
  - One compact table: Image + Product name | Marketplace & region | Category | Price | Rank or listing date | Evidence | View
    product.
  - Every row expands to show the full title, full-size images, specs, variants, seller, MOQ, price tiers and all source links.
  - Run logs and costs are behind **Run details**.
- **Plain evidence labels**, for example "Best Sellers #8 · observed 19 Nov 2026", "First available · 6 Nov 2026" and
  "New Releases #24 · listing date unavailable". The actual storefront (for example Amazon UAE) is shown on every row.
- **PDF is optional:** use the dashboard's Print / PDF button, or `report <job-id> --pdf`. CSV and JSON downloads are kept.

## 1.1.0 — 2026-11-19
`/leadsbounty scrap` is now a **marketplace product discovery catalog**. You review the products and decide what to sell.

- **Four questions:** scrap asks for country (or worldwide), period, category and number of products, all in one message. It asks only for what is missing, then shows the planned split.
- **Sources:** every search attempts both Amazon and Alibaba.
  - Amazon: Best Sellers and New Releases category pages for the requested country's storefront.
  - Alibaba: category search, plus subcategories taken from Alibaba's own category paths, so there is no fixed keyword list.
  - Specialist marketplaces (for example AutoZone for automotive) are tried once and skipped if they return nothing.
  - Which sources returned data is recorded and shown.
- **Country vs storefront:** the requested country is kept separate from the storefront's region. For Lebanon, Amazon UAE is labelled as a regional reference.
- **Split:** the total is split into Top Sellers and Newly Listed; an odd extra goes to Top Sellers. Each distinct product counts once and keeps all its links and badges.
- **Evidence:**
  - Top Sellers count as verified only when the source ranking covers the period. Amazon's current ranking is shown as "Current bestseller — observed today".
  - Newly Listed count as verified only with the marketplace's own date ("Date First Available", read in English or Arabic). Otherwise the product shows as "New Arrival — date unavailable".
- **Report:** product details include description, features, specs, images, variants, pack contents, seller or supplier, brand, price with currency (and where the currency came from), rating, reviews, stock and identifiers. Fields that weren't returned are marked "not returned".
  - Outputs are HTML, PDF, `catalog.csv`, and `catalog.json` with the complete raw records.
  - Extra candidates are saved but not shown in the requested results. Shortfalls are reported with exact counts and reasons.
- **Removed from scrap:** ready-to-test gates, COD calculations, mandatory local prices, demand checks and recommendations. It works with an Apify key alone.
- **Fixes:**
  - folder names with spaces;
  - the home credentials folder being taken for a project;
  - demo runs counting toward the live daily run cap;
  - sources that return nothing are now switched off.

## 1.0.0 — 2026-11-19
First community release. Published by Leads Bounty LLC.

- Claude Code plugin `leadsbounty` in marketplace `leadsbounty-community`.
- Native skills: `/leadsbounty:setup`, `:scrap`, `:status`, `:resume`, `:report`.
- `/leadsbounty:setup` installs the project command `/leadsbounty <cmd>`. Safe to rerun. An existing unrelated `leadsbounty` skill is kept and `/leadsbounty-research` is installed instead.
- SQLite job queue and a detached background worker that keeps running after Claude Code closes.
- Apify REST client for `apify/e-commerce-scraping-tool`:
  - validates every payload against the live input schema and records the actor build;
  - submits async batches and polls with backoff;
  - reads datasets page by page from a checkpoint;
  - handles 429 and 5xx responses;
  - reconciles a lost submission before any paid retry.
- Daily spend cap, daily run cap, concurrency limit, per-run `maxTotalChargeUsd` and discovery target. Actual and estimated costs are shown separately.
- Cross-batch and cross-day deduplication by identifiers (GTIN, ASIN, brand+model), specs and pack contents. Uncertain matches are tracked separately, and seller offers are kept.
- Countries: Lebanon (USD 29 minimum), Morocco, Saudi Arabia and Oman, with local-language terms. New-product yield is monitored, and the search rotates to fresh categories when it falls.
- Demand adapter: Google Trends via SerpApi (`SERPAPI_API_KEY`). An unavailable provider is marked explicitly.
- Qualification checks independent demand, a local listing, an exact supplier variant, a minimum price and COD break-even CPL, with a reason for every pass and rejection.
- Shortlist image check and supplier variant check.
- Branded HTML report, printable PDF (via local Chrome/Edge or the built-in fallback) and CSV evidence export. Partial runs show exact counts, the stopping reason and a resume command.
- Marketing angles are written only after qualification: in session, or unattended with `ANTHROPIC_API_KEY`.
- Free fixture demo (`--fixture`) and a 10-product live pilot (`pilot`).
