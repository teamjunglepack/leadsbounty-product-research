---
name: {{NAME}}
description: "Leads Bounty Product Research. Usage: /{{NAME}} scrap (asks market, period, category, target; runs only after you choose Start search) | /{{NAME}} status [job-id] | /{{NAME}} resume <job-id> | /{{NAME}} report <job-id> | /{{NAME}} setup"
argument-hint: "scrap [market] [target] [period] [category] | status [job-id] | resume <job-id> | report <job-id>"
disable-model-invocation: true
---
{{MARKER}}
<!-- Installed by /leadsbounty:setup (v{{VERSION}}). Rerunning setup refreshes this file. -->

# Leads Bounty Product Research

This project command uses the same implementation and background worker as the plugin's native `/leadsbounty:<command>` skills.

The member typed: `$ARGUMENTS`

## If the first word is `scrap` (marketplace discovery)

There is no default market. Never assume a country, period, category or target.

1. Work out which of these four inputs the member already gave:
   - **market**: a country or `Worldwide`
   - **period**: `Current snapshot`, or a date range (`last 7 days`, `last 30 days`, `YYYY-MM-DD to YYYY-MM-DD`)
   - **category**: for example automotive, health, kitchen, `all`, or `automotive/<subcategory>`
   - **target**: how many products the report should contain

2. If ANY are missing, ask for ONLY the missing ones, all in ONE message (use AskUserQuestion when available), then wait:
   - Which market: a country, or Worldwide?
   - Which period: Current snapshot, or a date range (last 7 days, last 30 days, or custom dates)?
   - Which category or subcategory? (automotive, health, kitchen, … or all)
   - How many products should the report contain?

3. Preview. This is free and queues nothing (use `python3` if `python` is not found):
   ```
   python .leadsbounty/lb.py scrap --country "<market>" --period "<period>" --category "<category>" --total <target> --via project
   ```
   Add `--fixture` only for the free demo. If the output starts with `MISSING_FIELDS` or `INVALID`, ask for that one input and
   preview again. Otherwise show the selections, split and sources.

4. Ask the member to choose **Start search** or **Change selections**. Only on Start search, rerun the same command with
   `--start` added and show the output. Never add `--start` on your own, during setup, when opening a report, or while testing.

## Any other command

Run (use `python3` if `python` is not found):

```
python .leadsbounty/lb.py $ARGUMENTS --via project
```

Show the output as-is. `report <job-id>` rebuilds and opens the HTML dashboard. Add `--pdf` only if the member wants a print
export.

## Rules

- Never ask the member to paste API keys, tokens or passwords into this chat. Missing credentials go in their private credentials file, edited on their own computer.
- Never research through a browser. The worker uses the Apify API.
- Do not change budget settings unless the member explicitly asks.
- Marketplace discovery is a catalog. Do not add sales recommendations, demand verdicts or profit calculations.
