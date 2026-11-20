"""Marketing angles, generated ONLY for qualified products.

Two ways to generate:
1. Unattended (worker): set ANTHROPIC_API_KEY in your credentials file. This uses the Claude API and is
   billed to that API key's Anthropic Console account, separately from any Claude subscription.
2. In session (no extra key): '/leadsbounty report <job-id>' asks Claude Code to write the angles from the
   evidence and store them with 'creative-put'. This uses your normal Claude Code usage.
"""
import json
import urllib.request

from . import db

MODEL = "claude-sonnet-5-5"


def context(con, cfg, job_id, product_id):
    p = con.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
    job = con.execute("SELECT country FROM jobs WHERE id=?", (job_id,)).fetchone()
    q = con.execute("SELECT * FROM qualifications WHERE job_id=? AND product_id=?", (job_id, product_id)).fetchone()
    offers = [dict(o) for o in con.execute("SELECT title, seller, price, currency, description, features, url FROM offers WHERE product_id=? AND kind='retail' LIMIT 6", (product_id,))]
    ev = [dict(e) for e in con.execute("SELECT kind, status, value, source_url FROM evidence WHERE job_id=? AND product_id=?", (job_id, product_id))]
    country = cfg["countries"][job["country"]]
    return {"product": {"title": p["title"], "variant": p["variant"], "pack_count": p["pack_count"]},
            "country": country["name"], "dialect": country.get("dialect"),
            "qualification": json.loads(q["reasons"]) if q else [], "offers": offers, "evidence": ev}


def validate_angles(angles):
    if not isinstance(angles, list) or len(angles) != 3:
        raise ValueError("exactly three angles required")
    for a in angles:
        for k in ("hook", "demo", "observation"):
            if not isinstance(a, dict) or not str(a.get(k, "")).strip():
                raise ValueError("each angle needs non-empty hook, demo and observation")
    return [{"hook": a["hook"].strip(), "demo": a["demo"].strip(), "observation": a["observation"].strip(),
             "hook_translation": str(a.get("hook_translation", "")).strip()} for a in angles]


def save(con, job_id, product_id, angles, generator):
    q = con.execute("SELECT status FROM qualifications WHERE job_id=? AND product_id=?", (job_id, product_id)).fetchone()
    if not q or q["status"] != "QUALIFIED":
        raise ValueError("creative content is only generated for QUALIFIED products")
    con.execute("INSERT OR REPLACE INTO creatives VALUES (?,?,?,?,?)", (job_id, product_id, db.j(angles), generator, db.now()))


PROMPT = """You write COD (cash on delivery) ad angles for {country}. Product: {title} ({variant}).
Evidence (use ONLY these observations, never invent facts):
{evidence}
Return JSON only: a list of exactly 3 objects with keys:
 "hook": a short spoken hook in {dialect} (Arabic script), "hook_translation": English translation,
 "demo": one filmable product demo a creator can shoot on a phone,
 "observation": the specific observed fact above that supports this angle (quote the value and source)."""


class ClaudeApiCreative:
    name = "claude-api:" + MODEL

    def __init__(self, api_key):
        self.api_key = api_key

    def generate(self, ctx):
        prompt = PROMPT.format(country=ctx["country"], title=ctx["product"]["title"], variant=ctx["product"]["variant"] or "standard",
                               dialect=ctx["dialect"], evidence=json.dumps({"qualification": ctx["qualification"], "offers": ctx["offers"]},
                                                                           ensure_ascii=False)[:6000])
        body = json.dumps({"model": MODEL, "max_tokens": 1500, "messages": [{"role": "user", "content": prompt}]}).encode()
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, method="POST", headers={
            "x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read())
        text = "".join(b.get("text", "") for b in data.get("content", []))
        start, end = text.find("["), text.rfind("]")
        return json.loads(text[start:end + 1])
