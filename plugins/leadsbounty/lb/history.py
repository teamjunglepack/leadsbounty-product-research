"""Scrape history across every research job in the member's local database.

Spend is counted once per provider run ID (retries, reconciles and resumes can touch the same run more than once).
Confirmed spend = actual charges reported by Apify. Estimates and unknown charges are kept separate.
"""
import json

from . import db

STOREFRONT = {"www.amazon.ae": "Amazon UAE", "www.amazon.sa": "Amazon Saudi Arabia", "www.amazon.com": "Amazon US",
              "www.amazon.co.uk": "Amazon UK", "www.amazon.fr": "Amazon France", "www.amazon.eg": "Amazon Egypt",
              "www.alibaba.com": "Alibaba", "www.autozone.com": "AutoZone", "www.cvs.com": "CVS", "www.boots.com": "Boots"}


def _task_label(b, task):
    if task:
        what = task.get("keyword") and "query “%s”" % task["keyword"] or task.get("url")
        return "%s · %s · %s" % (STOREFRONT.get(task.get("marketplace"), task.get("marketplace")), task.get("list_type", "").replace("_", " "), what)
    try:
        inp = json.loads(b["input_json"])
    except ValueError:
        inp = {}
    if inp.get("listingUrls"):
        return "page %s" % inp["listingUrls"][0].get("url")
    if inp.get("keyword"):
        return "%s · query “%s”" % (", ".join(STOREFRONT.get(m, m) for m in inp.get("marketplaces") or []), inp["keyword"])
    if inp.get("searchEngineKeyword"):
        return "Google Shopping (%s) · query “%s”" % (inp.get("countryCode"), inp["searchEngineKeyword"])
    return b["purpose"]


def build(con, cfg):
    jobs = []
    seen_runs = {}
    for j in con.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall():
        cp = db.unj(j["checkpoint"], {}) or {}
        params = cp.get("params") or {}
        tasks = cp.get("task_by_batch", {})
        runs, coverage = [], {}
        for b in con.execute("SELECT * FROM batches WHERE job_id=? ORDER BY seq", (j["id"],)).fetchall():
            task = tasks.get(b["id"])
            mk = (task or {}).get("marketplace") or ((json.loads(b["input_json"]).get("marketplaces") or [None])[0]
                                                     if b["input_json"] else None)
            if not mk:
                inp = json.loads(b["input_json"])
                url = (inp.get("listingUrls") or [{}])[0].get("url", "")
                mk = url.split("/")[2] if url.startswith("http") else ("Google Shopping" if inp.get("searchEngineKeyword") else "unknown")
            c = coverage.setdefault(STOREFRONT.get(mk, mk), 0)
            coverage[STOREFRONT.get(mk, mk)] = c + (b["ingest_offset"] or 0)
            rid = b["apify_run_id"]
            fixture = j["mode"] == "fixture" or (rid or "").startswith("fx-")
            if fixture:
                charge, kind = 0.0, "fixture"
            elif b["actual_cost"] is not None:
                charge, kind = b["actual_cost"], "confirmed"
            elif rid and b["status"] in ("submitted", "running", "succeeded"):
                charge, kind = b["est_cost"], "estimate (run not finished)"
            elif rid:
                charge, kind = None, "unknown"
            else:
                charge, kind = 0.0, "not submitted"
            dup = bool(rid) and rid in seen_runs
            if rid and not dup:
                seen_runs[rid] = {"charge": charge, "kind": kind, "job": j["id"]}
            runs.append({"batch": b["id"], "run_id": rid, "what": _task_label(b, task), "status": b["status"], "raw_rows": b["ingest_offset"] or 0,
                         "new_products": b["new_products"],
                         "dup_rate": round(1 - (b["new_products"] or 0) / b["ingest_offset"], 3) if b["ingest_offset"] else None,
                         "cost_per_new": round(charge / b["new_products"], 4) if (kind == "confirmed" and b["new_products"]) else None,
                         "charge": charge, "charge_kind": kind, "submitted": b["submit_attempt_at"],
                         "duplicate_run_id": dup, "attempts": b["attempts"]})
        uniq = {r["run_id"]: r for r in runs if r["run_id"] and not r["duplicate_run_id"]}.values()
        confirmed = sum(r["charge"] for r in uniq if r["charge_kind"] == "confirmed")
        est = sum(r["charge"] or 0 for r in uniq if r["charge_kind"].startswith("estimate"))
        unknown = sum(1 for r in uniq if r["charge_kind"] == "unknown")
        discovered = con.execute("SELECT COUNT(*) FROM job_products WHERE job_id=? AND relation!='uncertain'", (j["id"],)).fetchone()[0]
        code = params.get("target") or j["country"]
        market = "Worldwide" if code == "WW" else cfg["countries"].get(code, {}).get("name", code)
        jobs.append({"id": j["id"], "date": j["created_at"], "updated": j["updated_at"], "mode": j["mode"],
                     "kind": "discovery catalog" if params.get("kind") == "catalog" else "qualification (v1.0)",
                     "market": market, "category": params.get("category_text") or "—", "period": params.get("period_text") or "—",
                     "target": params.get("total") or j["target"], "discovered": discovered, "status": j["status"],
                     "stop": j["stop_reason"], "resumed": con.execute("SELECT COUNT(*) FROM events WHERE job_id=? AND msg LIKE 'resume via%'",
                                                                     (j["id"],)).fetchone()[0],
                     "coverage": coverage, "runs": runs, "unique_runs": len(uniq), "confirmed": round(confirmed, 6),
                     "estimated": round(est, 6), "unknown": unknown})
    ledger = con.execute("SELECT COALESCE(SUM(amount),0) FROM spend WHERE kind='actual'").fetchone()[0]
    total_confirmed = sum(v["charge"] for v in seen_runs.values() if v["kind"] == "confirmed")
    return {"jobs": jobs,
            "totals": {"confirmed": round(total_confirmed, 6), "estimated": round(sum(v["charge"] or 0 for v in seen_runs.values()
                                                                                       if v["kind"].startswith("estimate")), 6),
                       "unknown_runs": sum(1 for v in seen_runs.values() if v["kind"] == "unknown"),
                       "unique_paid_runs": sum(1 for v in seen_runs.values() if v["kind"] == "confirmed"),
                       "ledger_actual": round(ledger, 6),
                       "note": "Runs started outside the plugin (for example manual tests in the Apify console) are not in this database."}}
