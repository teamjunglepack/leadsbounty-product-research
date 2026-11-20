---
name: report
description: Open or refresh a Leads Bounty Product Research report (HTML, PDF, CSV, JSON) for a job. Discovery catalogs need nothing else; older qualification jobs can also get marketing angles.
argument-hint: "<job-id>"
---

# Leads Bounty Product Research — report

## How to run it

Run exactly this command with the Bash tool from the project root (use `python3` instead of `python` if `python` is not found, e.g. on macOS/Linux):

```
python "${CLAUDE_PLUGIN_ROOT}/scripts/lb.py" report $ARGUMENTS --via native
```

Show the command output to the member as-is (it is already branded as Leads Bounty Product Research).

## Rules
- Never ask the member to paste API keys, tokens or passwords into this chat. If credentials are missing, tell them to open their private credentials file (path is printed by the command) in a text editor on their own computer.
- Never start research through a browser. All research runs through the background worker and the Apify API.
- Do not change budget settings unless the member explicitly asks.

## Marketing angles (only after qualification)

If the output contains `CREATIVE_TODO`, then for EACH product in that JSON list:
1. Write exactly 3 angles as a JSON list of objects with keys `hook`, `hook_translation`, `demo`, `observation`.
   - `hook`: a short spoken hook in the product's `dialect` (Arabic script), natural for that country.
   - `demo`: one filmable demo a creator can shoot on a phone.
   - `observation`: the exact observed fact from the product's `qualification`/`offers` that supports the angle (quote the value and its source). Never invent facts, prices, claims or reviews.
2. Save the JSON to a temporary file and store it:
   `python "<same lb.py path as above>" creative-put <job-id> <product_id> <temp-file>`
3. Delete the temporary file.
Never write angles for products that are not QUALIFIED.

