---
name: resume
description: Resume a partial Leads Bounty Product Research job from its checkpoint without duplicating charges or counts.
argument-hint: "<job-id>"
disable-model-invocation: true
---

# Leads Bounty Product Research — resume

## How to run it

Run exactly this command with the Bash tool from the project root (use `python3` instead of `python` if `python` is not found, e.g. on macOS/Linux):

```
python "${CLAUDE_PLUGIN_ROOT}/scripts/lb.py" resume $ARGUMENTS --via native
```

Show the command output to the member as-is (it is already branded as Leads Bounty Product Research).

## Rules
- Never ask the member to paste API keys, tokens or passwords into this chat. If credentials are missing, tell them to open their private credentials file (path is printed by the command) in a text editor on their own computer.
- Never start research through a browser. All research runs through the background worker and the Apify API.
- Do not change budget settings unless the member explicitly asks.
