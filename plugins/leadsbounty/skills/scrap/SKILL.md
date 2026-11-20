---
name: scrap
description: Leads Bounty marketplace product discovery. Asks for any missing market, period, category and target number of products, shows the selections, and starts a paid background search (Amazon + Alibaba via the member's Apify key) only after the member chooses Start search.
argument-hint: "[market] [target] [period] [category] [--fixture]"
disable-model-invocation: true
---

# Leads Bounty Product Research — marketplace discovery

The member supplied: `$ARGUMENTS`

There is no default market. Never assume a country, period, category or target.

## 1. Collect the four inputs

Work out which of these the member already gave:

- **market**: a country (for example Saudi Arabia, Morocco, Oman, Lebanon, UAE, Egypt) or `Worldwide`
- **period**: `Current snapshot`, or a date range: `last 7 days`, `last 30 days`, or `YYYY-MM-DD to YYYY-MM-DD`
- **category**: for example automotive, health, kitchen, beauty, electronics, home, baby, toys, sports, pet, `all`, or a subcategory such as `automotive/car electronics`
- **target**: how many products the report should contain

If ANY are missing, ask for ONLY the missing ones, all in ONE message (use the AskUserQuestion tool when it is available), then wait:

- Which market: a country, or Worldwide?
- Which period: Current snapshot, or a date range (last 7 days, last 30 days, or custom dates)?
- Which category or subcategory? (automotive, health, kitchen, … or all)
- How many products should the report contain?

## 2. Show the selections (free, nothing is queued)

Run this from the project root with the Bash tool (use `python3` instead of `python` if `python` is not found):

```
python "${CLAUDE_PLUGIN_ROOT}/scripts/lb.py" scrap --country "<market>" --period "<period>" --category "<category>" --total <target> --via native
```

Add `--fixture` only if the member asked for the free demo. Without `--start` this command only previews; it spends nothing.

- If the output starts with `MISSING_FIELDS` or `INVALID`, ask for that one input and run the preview again.
- Otherwise show the member the selections, the planned split and the sources from the output.

## 3. Start search, only on the member's explicit choice

Ask the member to choose **Start search** or **Change selections** (use AskUserQuestion when available). Only if they choose
Start search, rerun the exact same command with `--start` added, and show the output.

Never add `--start` on your own, during setup, when opening a report or dashboard, or while testing.

## Rules

- Never ask the member to paste API keys into this chat. If a key is missing, tell them to edit their private credentials file in a text editor on their own computer.
- Never research through a browser. The worker uses the Apify API.
- Do not change budget settings unless the member explicitly asks.
- This is a discovery catalog. Do not add sales recommendations, demand verdicts or profit calculations.
