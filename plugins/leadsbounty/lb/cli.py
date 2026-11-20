"""Single implementation behind /leadsbounty <cmd> and /leadsbounty:<cmd>."""
import argparse
import json
import os
import subprocess
import sys
import uuid

from . import __version__, brand, db, worker
from .config import (PLUGIN_ROOT, Paths, credential_status, credentials_file, find_project_dir, load_config,
                     load_credentials, resolve_country, set_config_value)


def _paths(args):
    p = Paths(find_project_dir(getattr(args, "project", None)))
    p.ensure()
    return p


def _con(paths):
    return db.connect(paths.db)


# ---------------------------------------------------------------------------
def ensure_worker(paths, con):
    alive, pid = worker.worker_alive(con)
    if alive:
        return "running (pid %s)" % pid
    if os.environ.get("LEADSBOUNTY_NO_AUTOSTART"):
        return "not started (LEADSBOUNTY_NO_AUTOSTART is set)"
    log = open(os.path.join(paths.logs, "worker.log"), "a", encoding="utf-8")
    cmd = [sys.executable, os.path.join(PLUGIN_ROOT, "scripts", "lb.py"), "worker", "--project", paths.project]
    kw = dict(stdin=subprocess.DEVNULL, stdout=log, stderr=log, cwd=paths.project, close_fds=True)
    if os.name == "nt":
        DETACHED, NEW_GROUP, NO_WINDOW, BREAKAWAY = 0x00000008, 0x00000200, 0x08000000, 0x01000000
        try:
            proc = subprocess.Popen(cmd, creationflags=DETACHED | NEW_GROUP | NO_WINDOW | BREAKAWAY, **kw)
        except OSError:
            proc = subprocess.Popen(cmd, creationflags=DETACHED | NEW_GROUP | NO_WINDOW, **kw)
    else:
        proc = subprocess.Popen(cmd, start_new_session=True, **kw)
    return "started (pid %s)" % proc.pid


def cmd_setup(args):
    from . import setup_project
    _, text = setup_project.run(find_project_dir(args.project))
    print(text)
    return 0


def cmd_scrap(args, pilot=False):
    """Marketplace discovery catalog. Prints MISSING_FIELDS (exit 4) so the skill can ask the member."""
    from . import catalog
    paths = _paths(args)
    cfg = load_config(paths)
    words = list(args.words or [])
    total = args.total
    if total is None and words and words[-1].isdigit():
        total = int(words.pop())
    country = args.country or (" ".join(words) if words else None)
    if pilot and total is None:
        total = 10
    if pilot:
        total = min(total, 10)
    missing = [n for n, v in (("country", country), ("period", args.period), ("category", args.category), ("total", total)) if not v]
    if missing:
        print("MISSING_FIELDS: %s" % ", ".join(missing))
        print("Ask the member (in one message) for: " + "; ".join({
            "country": "Which market: a country, or Worldwide?",
            "period": "Which period? Current snapshot, or a date range (last 7 days, last 30 days, YYYY-MM-DD to YYYY-MM-DD)",
            "category": "Which category or subcategory? e.g. automotive, health, kitchen, or all",
            "total": "How many products should the report contain (target number)?"}[m] for m in missing))
        return 4
    try:
        code, cname = catalog.resolve_target(cfg, country)
        period = catalog.parse_period(args.period)
        category = catalog.parse_category(args.category)
    except ValueError as e:
        print("INVALID: %s" % e)
        return 2
    if total < 1:
        print("INVALID: total must be at least 1")
        return 2
    mode = "fixture" if args.fixture else "live"
    q = catalog.split_total(total)
    print(brand.banner_text("Marketplace discovery%s" % (" · FIXTURE DEMO (free, synthetic)" if mode == "fixture" else "")))
    print("Market: %s%s" % (cname, "" if code == "WW" else " (%s)" % code))
    if period.get("snapshot"):
        print("Period: Current snapshot (today's rankings; Newly Listed = %s)" % period["new_rule"])
    else:
        print("Period: %s (%s to %s)" % (period["label"], period["start"], period["end"]))
    print("Category: %s" % category["label"])
    print("PLANNED SPLIT: %d distinct products = %d Top Sellers + %d Newly Listed" % (total, q["top"], q["new"]))
    print("Sources (both attempted on every search):")
    for mk, region, rel in catalog.storefronts_for(cfg, code):
        print("  - Amazon %s: Best Sellers + New Releases category pages · %s" % (mk, rel))
    print("  - Alibaba www.alibaba.com: category search pages + subcategories found on Alibaba · global wholesale")
    for c in category["categories"]:
        for sp in catalog.CATEGORIES[c].get("specialist", []):
            print("  - Specialist %s (%s): tried once; skipped if it returns nothing" % (sp["marketplace"], sp["label"]))
    if not catalog.top_period_ok(period):
        print("Note: Amazon Best Sellers is a current ranking; it is shown as 'Current bestseller - observed today' and is not counted as a verified %s top seller." % period["label"])
    if mode == "live":
        problems = []
        if not load_credentials().get("APIFY_TOKEN"):
            problems.append("APIFY_TOKEN is missing. Add it to %s (open it in a text editor; never paste it in chat)." % credentials_file())
        if cfg["budget"].get("daily_spend_cap_usd") is None:
            problems.append("No daily spend cap. Set one, e.g.: /leadsbounty config set budget.daily_spend_cap_usd 5")
        if pilot and not cfg["budget"].get("confirmed"):
            problems.append("Budget not confirmed for live runs. After reviewing it: /leadsbounty config set budget.confirmed true")
        if problems:
            print("Cannot start live research yet:\n- " + "\n- ".join(problems))
            print("Free alternative: add --fixture")
            return 3
    if not args.start:
        print("PREVIEW ONLY: nothing was queued and nothing was spent.")
        print("START_SEARCH: rerun the same command with --start to begin%s." % (" (paid: uses your Apify budget)" if mode == "live" else ""))
        return 0
    params = {"kind": "catalog", "target": code, "country_text": country, "period_text": args.period, "category_text": args.category,
              "total": total, "requested_on": period["end"].isoformat()}
    con = _con(paths)
    sig = db.j({k: params[k] for k in ("target", "period_text", "category_text", "total")})
    existing = None
    for r in con.execute("SELECT id, checkpoint FROM jobs WHERE mode=? AND status IN ('queued','running')", (mode,)).fetchall():
        pp = (db.unj(r["checkpoint"], {}) or {}).get("params", {})
        if pp.get("kind") == "catalog" and db.j({k: pp.get(k) for k in ("target", "period_text", "category_text", "total")}) == sig:
            existing = r["id"]
    if existing:
        print("The same search is already running: %s. Not starting a duplicate." % existing)
        job_id = existing
    else:
        job_id = "LB-%s-%s-%s" % (code, db.today().replace("-", ""), uuid.uuid4().hex[:6])
        con.execute("INSERT INTO jobs(id,country,target,mode,status,phase,checkpoint,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (job_id, code, total, mode, "queued", "discovery", db.j({"params": params}), db.now(), db.now()))
        print("Queued job %s" % job_id)
    db.log(con, job_id, "command received via %s" % (args.via or "cli"))
    print("Worker: %s" % ensure_worker(paths, con))
    print("You can close Claude Code now. Check progress any time with: /leadsbounty status %s" % job_id)
    return 0


def cmd_status(args):
    paths = _paths(args)
    con = _con(paths)
    alive, pid = worker.worker_alive(con)
    if not alive and con.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running')").fetchone():
        # e.g. after the computer slept or restarted: pick up where the checkpoint left off
        print("Worker was not running for an active job; restarting it: %s" % ensure_worker(paths, con))
        alive, pid = True, None
    cfg = load_config(paths)
    if not args.job:
        print(brand.banner_text("Status"))
        print("Worker: %s" % ("running (pid %s)" % pid if alive else "not running (starts automatically with scrap/resume)"))
        s = db.spend_today(con)
        print("Today's spend: actual USD %.4f + estimated USD %.4f (cap: %s)" % (s["actual"], s["estimate"], cfg["budget"].get("daily_spend_cap_usd")))
        rows = con.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT 20").fetchall()
        if not rows:
            print("No jobs yet. Start one with: /leadsbounty scrap")
        for r in rows:
            c = db.job_counts(con, r["id"])
            print("%-34s %-7s %-8s %-9s %4d/%-4d new  %s" % (r["id"], r["mode"], r["status"], r["phase"], c["new_products"], r["target"],
                                                          r["stop_reason"] or ""))
        return 0
    job = con.execute("SELECT * FROM jobs WHERE id=?", (args.job,)).fetchone()
    if not job:
        print("Unknown job %s" % args.job)
        return 2
    c = db.job_counts(con, args.job)
    cpj = db.unj(job["checkpoint"], {}) or {}
    if cpj.get("params", {}).get("kind") == "catalog":
        from . import catalog
        import datetime as _dt
        pp = cpj["params"]
        per = catalog.parse_period(pp["period_text"], _dt.date.fromisoformat(pp["requested_on"]))
        sel = catalog.select(catalog.classify(con, args.job, per), pp["total"])
        print(brand.banner_text("Discovery job %s%s" % (args.job, " · FIXTURE DEMO" if job["mode"] == "fixture" else "")))
        print("%s · %s · %s · status %s · phase %s · worker %s" % (pp["country_text"], pp["category_text"], per["label"], job["status"],
                                                                  job["phase"], "running" if alive else "stopped"))
        q = sel["quota"]
        print("  Top Sellers verified      %d / %d" % (len(sel["groups"]["top"]), q["top"]))
        print("  Newly Listed verified     %d / %d" % (len(sel["groups"]["new"]), q["new"]))
        print("  Current bestsellers (observed today, not verified for period)  %d" % len(sel["current_bestsellers"]))
        print("  New arrivals, date unavailable                                  %d" % len(sel["new_undated"]))
        print("  Extra candidates saved %d · raw rows %d · duplicate offers %d" % (len(sel["extras"]), c["raw_rows"] or con.execute(
            "SELECT COUNT(*) FROM raw_rows WHERE job_id=?", (args.job,)).fetchone()[0], c["duplicates"]))
        for k, cv in sorted((cpj.get("coverage") or {}).items()):
            print("  source %-40s runs %d rows %d%s" % (k, cv["runs"], cv["rows"], "  (switched off: %s)" % cpj["dead_sources"][k] if k in cpj.get("dead_sources", {}) else ""))
        if job["stop_reason"]:
            print("Stop: %s" % job["stop_reason"])
        for s_ in db.job_spend(con, args.job):
            print("  spend %-10s %-8s USD %.4f" % (s_["provider"], s_["kind"], s_["s"]))
        if job["report_dir"]:
            print("Report: %s" % os.path.join(job["report_dir"], "report.html"))
        return 0
    print(brand.banner_text("Job %s%s" % (args.job, " · FIXTURE DEMO" if job["mode"] == "fixture" else "")))
    print("Country %s · target %d · status %s · phase %s · worker %s" % (job["country"], job["target"], job["status"], job["phase"],
                                                                          "running" if alive else "stopped"))
    if job["stop_reason"]:
        print("Stopping reason: %s" % job["stop_reason"])
    for k, v in c.items():
        print("  %-20s %s" % (k, v))
    for s in db.job_spend(con, args.job):
        print("  spend %-24s %-8s USD %.4f" % (s["provider"], s["kind"], s["s"]))
    if job["actor_build"]:
        print("Actor build: %s" % json.loads(job["actor_build"]).get("build_number"))
    print("Recent events:")
    for e in con.execute("SELECT ts, level, msg FROM events WHERE job_id=? ORDER BY id DESC LIMIT 8", (args.job,)).fetchall()[::-1]:
        print("  %s [%s] %s" % (e["ts"][11:19], e["level"], e["msg"].splitlines()[0][:150]))
    if job["report_dir"]:
        print("Report: %s" % os.path.join(job["report_dir"], "report.html"))
    return 0


def cmd_resume(args):
    paths = _paths(args)
    con = _con(paths)
    job = con.execute("SELECT * FROM jobs WHERE id=?", (args.job,)).fetchone()
    if not job:
        print("Unknown job %s" % args.job)
        return 2
    if job["status"] in ("queued", "running"):
        print("Job %s is already active." % args.job)
    else:
        c = db.job_counts(con, args.job)
        is_catalog = (db.unj(job["checkpoint"], {}) or {}).get("params", {}).get("kind") == "catalog"
        phase = "discovery" if is_catalog or c["new_products"] < job["target"] else "evidence"
        cp = db.unj(job["checkpoint"], {}) or {}
        for k in ("shortlist", "supplier_skipped", "discovery_stop"):
            cp.pop(k, None)
        con.execute("UPDATE jobs SET status='queued', phase=?, stop_reason=NULL, checkpoint=?, updated_at=? WHERE id=?",
                    (phase, db.j(cp), db.now(), args.job))
        con.execute("DELETE FROM qualifications WHERE job_id=?", (args.job,))
        print("Resuming %s from checkpoint (plan item %s, %d/%d new products)." % (args.job, cp.get("plan_index", 0), c["new_products"], job["target"]))
    db.log(con, args.job, "resume via %s" % (args.via or "cli"))
    print("Worker: %s" % ensure_worker(paths, con))
    return 0


def cmd_report(args):
    paths = _paths(args)
    con = _con(paths)
    job = con.execute("SELECT * FROM jobs WHERE id=?", (args.job,)).fetchone()
    if not job:
        print("Unknown job %s" % args.job)
        return 2
    if job["phase"] != "done" and not args.force:
        print("Job %s is still in phase '%s'. The report is written automatically when it finishes. Use --force for an interim report." % (args.job, job["phase"]))
        return 0
    from . import report, catalog_report
    is_catalog = (db.unj(job["checkpoint"], {}) or {}).get("params", {}).get("kind") == "catalog"
    if is_catalog:
        out = catalog_report.generate(con, load_config(paths), paths, args.job, pdf_export=args.pdf)
    else:
        out = report.generate(con, load_config(paths), paths, args.job)
    con.execute("UPDATE jobs SET report_dir=? WHERE id=?", (out, args.job))
    print(brand.banner_text("Report%s" % (" · FIXTURE DEMO" if job["mode"] == "fixture" else "")))
    files = (["report.html", "catalog.csv", "catalog.json"] + (["report.pdf"] if args.pdf else [])) if is_catalog else \
        ["report.html", "report.pdf", "evidence.csv"]
    for f in files:
        print("  %s" % os.path.join(out, f))
    if is_catalog:
        print("Open report.html in your browser: it is the product discovery dashboard. PDF is optional: add --pdf.")
    todo = [] if is_catalog else creative_todo(con, args.job)
    if todo:
        print("\n%d qualified product(s) still need marketing angles. CREATIVE_TODO follows (JSON):" % len(todo))
        print(json.dumps(todo, ensure_ascii=False, indent=1))
    return 0


def creative_todo(con, job_id):
    from . import creative
    cfg = None
    out = []
    for q in con.execute("""SELECT q.product_id FROM qualifications q LEFT JOIN creatives c ON c.job_id=q.job_id AND c.product_id=q.product_id
                            WHERE q.job_id=? AND q.status='QUALIFIED' AND c.product_id IS NULL""", (job_id,)).fetchall():
        if cfg is None:
            cfg = load_config(Paths(find_project_dir()))
        ctx = creative.context(con, cfg, job_id, q["product_id"])
        ctx["product_id"] = q["product_id"]
        for o in ctx["offers"]:
            o.pop("description", None)
        out.append(ctx)
    return out


def cmd_creative_put(args):
    from . import creative, report
    paths = _paths(args)
    con = _con(paths)
    raw = sys.stdin.read() if args.file == "-" else open(args.file, encoding="utf-8").read()
    try:
        angles = creative.validate_angles(json.loads(raw))
        creative.save(con, args.job, args.product, angles, "claude-code-session")
    except (ValueError, json.JSONDecodeError) as e:
        print("Rejected: %s" % e)
        return 2
    out = report.generate(con, load_config(paths), paths, args.job)
    print("Saved 3 angles for %s; report refreshed: %s" % (args.product, os.path.join(out, "report.html")))
    return 0


def cmd_config(args):
    paths = _paths(args)
    if args.action == "set":
        v = set_config_value(paths, args.key, args.value)
        print("Set %s = %r in %s" % (args.key, v, paths.config))
    else:
        cfg = load_config(paths)
        cur = cfg
        for k in (args.key.split(".") if args.key else []):
            cur = cur[k]
        print(json.dumps(cur, indent=2, ensure_ascii=False))
    return 0


def cmd_doctor(args):
    paths = _paths(args)
    print(brand.banner_text("Doctor · v%s" % __version__))
    print("Python: %s" % sys.version.split()[0])
    print("Project: %s" % paths.project)
    print("Plugin: %s" % PLUGIN_ROOT)
    print("Credentials file: %s" % credentials_file())
    for k, v in credential_status().items():
        print("  %-18s %s" % (k, v))
    cfg = load_config(paths)
    print("Daily cap: %s · run cap %s · concurrency %s" % (cfg["budget"]["daily_spend_cap_usd"], cfg["budget"]["daily_run_cap"],
                                                          cfg["budget"]["max_concurrent_runs"]))
    missing = [k for k in ("confirmation_rate", "delivery_rate", "delivery_fee_usd", "return_cost_usd", "fulfillment_fee_usd",
                           "shipping_to_country_per_unit_usd") if cfg["economics"].get(k) is None]
    print("COD assumptions missing: %s" % (", ".join(missing) or "none"))
    return 0


def cmd_worker(args):
    return worker.run(find_project_dir(args.project))


def build_parser():
    ap = argparse.ArgumentParser(prog="leadsbounty", description=brand.PRODUCT_NAME)
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd")

    def add(name, fn, **kw):
        p = sub.add_parser(name, **kw)
        p.add_argument("--project")
        p.add_argument("--via")
        p.set_defaults(fn=fn)
        return p

    add("setup", cmd_setup)
    for name, pilot in (("scrap", False), ("pilot", True)):
        p = add(name, (lambda a, pl=pilot: cmd_scrap(a, pl)))
        p.add_argument("words", nargs="*", metavar="country [total]")
        p.add_argument("--country")
        p.add_argument("--period")
        p.add_argument("--category")
        p.add_argument("--total", type=int)
        p.add_argument("--fixture", action="store_true")
        p.add_argument("--start", action="store_true", help="actually queue the search (otherwise preview only)")
    p = add("status", cmd_status)
    p.add_argument("job", nargs="?")
    p = add("resume", cmd_resume)
    p.add_argument("job")
    p = add("report", cmd_report)
    p.add_argument("job")
    p.add_argument("--force", action="store_true")
    p.add_argument("--pdf", action="store_true", help="also write an optional print PDF")
    p = add("creative-put", cmd_creative_put)
    p.add_argument("job")
    p.add_argument("product")
    p.add_argument("file")
    p = add("config", cmd_config)
    p.add_argument("action", choices=["get", "set"])
    p.add_argument("key", nargs="?")
    p.add_argument("value", nargs="?")
    add("doctor", cmd_doctor)
    add("worker", cmd_worker)
    return ap


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    argv = list(argv if argv is not None else sys.argv[1:])
    # A skill may pass "scrap <country> <total>" as ONE string; split only that leading command string.
    if argv and " " in argv[0] and argv[0].split()[0] in ("setup", "scrap", "pilot", "status", "resume", "report", "config", "doctor"):
        argv = argv[0].split() + argv[1:]
    if not argv or argv[0].startswith("--") and argv[0] not in ("--version", "--help", "-h"):
        argv = ["status"] + argv
    args = build_parser().parse_args(argv)
    if not hasattr(args, "fn"):
        build_parser().print_help()
        return 1
    return args.fn(args)
