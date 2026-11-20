# Leads Bounty Product Research — Claude Code plugin

**Published by Leads Bounty LLC · [leadsbounty.com](https://leadsbounty.com)** · version 1.2.0

A Claude Code plugin that builds a **marketplace product discovery catalog** from Amazon and Alibaba (Top Sellers and Newly
Listed, with source evidence) for any country or worldwide. It uses **your own Apify account**, so you control and pay for your
own searches. Results open as an HTML dashboard with CSV and JSON exports.

Full plugin documentation: [plugins/leadsbounty/README.md](plugins/leadsbounty/README.md).

## What you need

- **Claude Code** (CLI, desktop app or IDE extension)
- **Python 3.10+**, standard library only, nothing to `pip install`
- **An Apify account and API token**: [apify.com](https://apify.com). Live searches cost about USD 0.003–0.006 per product
  scraped, billed by Apify to your account.

## 1. Install

In Claude Code, run these one at a time:

```
/plugin marketplace add YOUR-GITHUB-USER/leadsbounty-product-research
```

```
/plugin install leadsbounty@leadsbounty-community
```

Then open the project folder where you want your research stored, and run:

```
/leadsbounty:setup
```

Setup creates a local `.leadsbounty/` folder (database, reports, settings) and installs the short project command
`/leadsbounty`. It is safe to rerun. If your project already has its own `leadsbounty` skill, setup leaves it alone and installs
`/leadsbounty-research` instead. The native names always work: `/leadsbounty:scrap`, `/leadsbounty:status`,
`/leadsbounty:resume`, `/leadsbounty:report`.

## 2. Add your Apify key privately

**Never paste your key into the Claude chat.** Setup creates a private credentials file in your home folder. Open it in a text
editor:

- Windows (PowerShell): `notepad $HOME\.leadsbounty\credentials.env`
- macOS: `open -e ~/.leadsbounty/credentials.env`
- Linux: `nano ~/.leadsbounty/credentials.env`

Fill in the line `APIFY_TOKEN=`, then save. Find your token in Apify Console > Settings > API & Integrations. An environment
variable named `APIFY_TOKEN` also works.

## 3. Set your budget (recommended)

```
/leadsbounty config set budget.daily_spend_cap_usd 5
```

The worker stops before a batch would exceed your daily cap. `budget.daily_run_cap` and `budget.max_concurrent_runs` can be
set the same way.

## 4. Try the free demo

```
/leadsbounty scrap worldwide 10 current snapshot automotive --fixture
```

The demo uses built-in sample data, costs nothing, and its report is labelled **FIXTURE DEMO**.

## 5. Run a search

```
/leadsbounty scrap
```

There is no default market. Claude asks for anything you haven't given, in one message:

- **Market:** a country, or Worldwide
- **Period:** Current snapshot, last 7 days, last 30 days, or `YYYY-MM-DD to YYYY-MM-DD`
- **Category:** for example automotive, health, kitchen, `all`, or a subcategory such as `automotive/car electronics`
- **Target:** how many products the report should contain

You can answer up front, for example `/leadsbounty scrap saudi arabia 20 current snapshot kitchen`.

You'll see a free preview of your selections, the planned split and sources. **Nothing is spent until you choose Start search.**
The search then runs in a background worker, so you can close Claude Code while it runs.

## Other commands

| Command | What it does |
|---|---|
| `/leadsbounty status` | All jobs, progress and spend. Restarts the worker after sleep or a reboot. |
| `/leadsbounty status <job-id>` | One job in detail |
| `/leadsbounty report <job-id>` | Rebuild and open the HTML dashboard. Add `--pdf` for an optional print PDF. |
| `/leadsbounty resume <job-id>` | Continue a job that stopped as Partial, for example at the daily cap |
| `/leadsbounty config get` / `config set <key> <value>` | View or change settings |

Reports are saved in `.leadsbounty/reports/<job-id>/`: `report.html` (dashboard), `catalog.csv`, `catalog.json` and an optional
`report.pdf`.

## Privacy

- Your API key stays in your own credentials file or environment. This repository contains no keys and no research data.
- Your research database and reports stay in your project's `.leadsbounty/` folder. Keep it out of version control.
- No browser is used for research; the worker calls the Apify API directly.

## Run the offline tests (optional, free)

```
python plugins/leadsbounty/tests/test_leadsbounty.py
```

The tests use synthetic fixtures and make no network calls.

---

*Leads Bounty Product Research · Published by Leads Bounty LLC · [leadsbounty.com](https://leadsbounty.com)*
