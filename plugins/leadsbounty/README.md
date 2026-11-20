# Leads Bounty Product Research

**Published by Leads Bounty LLC · [leadsbounty.com](https://leadsbounty.com)** · version 1.2.0 · see [CHANGELOG](CHANGELOG.md)

A Claude Code plugin that builds a **marketplace product discovery catalog**. You review the products and decide what to sell.

```
/leadsbounty scrap
```

There is no default market. The command asks only for what you haven't already given, in one message:

- **Market:** a country, or Worldwide.
- **Period:** Current snapshot, or a date range (last 7 days, last 30 days, or custom dates).
- **Category or subcategory:** automotive, health, kitchen, … or all.
- **Target:** how many products the report should contain.

You can answer up front, for example `/leadsbounty scrap saudi arabia 20 current snapshot kitchen`.

It then:

1. shows your selections and the planned split (half **Top Sellers**, half **Newly Listed**). Nothing runs and nothing is spent
   until you choose **Start search**;
2. after Start search, runs a background worker through **your own Apify account**;
3. builds `report.html`, the **Leads Bounty product discovery dashboard**. It has a **Products** tab (views All Discovered,
   Newly Listed, Top Selling and Unmatched; filters, price range and sorting) and a **Scrape Report** tab (runs, spend and
   coverage across all your jobs), plus CSV and JSON downloads. A PDF is optional (Print / PDF button, or
   `/leadsbounty report <job-id> --pdf`).

**Periods**

- **Current snapshot:** today's Amazon Best Sellers ranks count as Top Sellers. Newly Listed means a first-available date within
  the last 30 days.
- **Date range:** only evidence that matches the range counts. Current-only rankings appear as additional discoveries.

Setup, status, opening a report and running the tests never start a paid search.

You can close Claude Code while it runs. Check progress any time with `/leadsbounty status`.

## 1. What you need

| Need | Why | Cost |
|---|---|---|
| Claude Code | runs the commands | your Claude plan |
| Python 3.10+ | runs the worker (standard library only) | free |
| Apify account and API token | Amazon and Alibaba discovery through the actor `apify/e-commerce-scraping-tool` | about USD 0.003–0.006 per product scraped |

No other keys are needed for discovery.

## 2. Install (once per computer)

In Claude Code:

```
/plugin marketplace add YOUR-GITHUB-ORG/YOUR-REPO
/plugin install leadsbounty@leadsbounty-community
/leadsbounty:setup
```

Setup is safe to rerun. If your project already has its own `leadsbounty` skill, setup keeps it and installs
`/leadsbounty-research` instead. The native spellings always work: `/leadsbounty:scrap`, `/leadsbounty:status`, `/leadsbounty:resume`,
`/leadsbounty:report`.

## 3. Add your Apify key privately (never in the chat)

Open your private credentials file:

- Windows PowerShell: `notepad $HOME\.leadsbounty\credentials.env`
- macOS: `open -e ~/.leadsbounty/credentials.env`
- Linux: `nano ~/.leadsbounty/credentials.env`

Fill in `APIFY_TOKEN=` (Apify Console > Settings > API & Integrations) and save. Environment variables with the same name also
work.

## 4. Set your budget

```
/leadsbounty config set budget.daily_spend_cap_usd 5
/leadsbounty config set budget.daily_run_cap 40
/leadsbounty config set budget.max_concurrent_runs 2
```

- Before each batch, the worker checks what has already been spent or committed today and stops when the cap would be exceeded.
- Each Apify run also carries Apify's own per-run limit (`maxTotalChargeUsd`). Apify requires that limit to be at least USD 1.00.
- Actual provider costs and estimates are shown separately.

## 5. Try it free

```
/leadsbounty scrap worldwide 10 current snapshot automotive --fixture
```

The demo uses built-in synthetic data. Its report is labelled **FIXTURE DEMO**.

## 6. How discovery works

**Sources.** Every search attempts both primary sources:

- **Amazon:** the Best Sellers and New Releases pages for the category on the storefront for your country. Individual product
  pages are then scraped for details.
- **Alibaba:** the category search. Subcategories found in Alibaba's own category paths are searched next, so you also see products
  you didn't think to search for.
- **Specialist marketplaces** where one exists for the category (AutoZone for automotive; CVS or Boots for health): tried once and
  skipped if they return nothing.

The dashboard's **Scrape Report** tab shows which sources returned data.

**Country vs storefront.** The requested country is kept separate from where the data comes from:

| You ask for | Amazon storefront used | Label |
|---|---|---|
| Lebanon, Oman | amazon.ae | regional reference (not Lebanon- or Oman-specific) |
| Morocco | amazon.fr | regional reference |
| Saudi Arabia | amazon.sa | local storefront |
| Worldwide | amazon.com, .co.uk, .ae, .sa | each storefront named on every product |

Alibaba is labelled "global wholesale" everywhere. Alibaba prices are wholesale unit prices; Amazon prices are retail prices.

**Split.** The total is split in half: 10 gives 5 + 5, 500 gives 250 + 250, and an odd extra slot goes to Top Sellers. A product
found on several lists or marketplaces counts once and keeps all its links and badges.

**Top Sellers (verified)** need a source ranking or sales metric whose time window fits your period.

- Amazon Best Sellers is a *current* ranking (updated hourly). It counts for a **Current snapshot** search. For a date range,
  those products are listed as additional discoveries ("Best Sellers #8 · observed today"), outside the requested-period count.
- Alibaba returns no sales ranking through this scraper.

**Newly Listed (verified)** need the marketplace's own listing date inside your period. Amazon's "Date First Available" field is
read in English or Arabic.

- A New Releases product without a date is listed as **"New Arrival — date unavailable"**.
- The date the plugin found a product, or its latest review date, is never treated as a listing date.

**Shortfalls and extras**

- If a group falls short, the report shows the exact shortfall and why.
- Extra scraped candidates are saved in the CSV and JSON exports, but the requested results never show more than you asked for.
- Products already found in earlier jobs are not counted again.

## 7. Commands

```
/leadsbounty scrap
/leadsbounty status
/leadsbounty status <job-id>
/leadsbounty report <job-id>
/leadsbounty resume <job-id>
```

Reports are in `.leadsbounty/reports/<job-id>/`:

- `report.html`: the product discovery dashboard; open it in your browser.
- `report.pdf`: optional; only with `--pdf` or the dashboard's Print / PDF button.
- `catalog.csv`: one row per source record.
- `catalog.json`: every product with its full raw marketplace records.

## 8. Sleep, shutdown, resume

- The worker is a separate process and keeps running after you close Claude Code.
- Every submitted run, dataset position and plan step is saved in `.leadsbounty/research.sqlite`.
- After sleep or a restart, run `/leadsbounty status`; it restarts the worker for active jobs.
- For a job marked partial, run `/leadsbounty resume <job-id>`.
- A run is recorded before it is submitted, and lost submissions are looked up on Apify before any retry. Restarts never cause
  duplicate charges or duplicate counts.

## 9. What the scraper can't provide (as of version 1.2.0)

- **Alibaba:** no MOQ, unit-price tiers, sales figures or listing dates. Supplier name, unit price and category path are returned.
- **Amazon:** no shipping details, and no time-windowed sales ranking.
- **Google Shopping:** under maintenance at the actor's provider at release time. It is not used.

Missing fields are shown as "not returned", never guessed.

## 10. Privacy

- Your key stays in your credentials file or environment.
- `.leadsbounty/` is git-ignored.
- The package contains no keys and no research data.
- No browser is used for research.

---

*Leads Bounty Product Research · Published by Leads Bounty LLC · [leadsbounty.com](https://leadsbounty.com)*
