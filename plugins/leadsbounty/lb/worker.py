"""Background worker. Runs detached from Claude Code; all state lives in SQLite so it can stop
and restart at any point (sleep, shutdown, crash) without duplicate charges or counts."""
import datetime as _dt
import hashlib
import json
import os
import statistics
import sys
import time
import traceback

from . import apify, db, dedupe, demand, qualify, verify
from .config import Paths, load_config, load_credentials

HEARTBEAT_STALE_SECONDS = 90
RECONCILE_GRACE_SECONDS = 180
IDLE_EXIT_SECONDS = 20
INGEST_PAGE = 200
ACTIVE = ("queued", "running")


def utcnow():
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)


def iso(dt):
    return dt.isoformat()


# ---------------------------------------------------------------------------
# worker lock / heartbeat
# ---------------------------------------------------------------------------
def worker_alive(con):
    r = con.execute("SELECT pid, heartbeat FROM worker WHERE id=1").fetchone()
    if not r or not r["heartbeat"]:
        return False, None
    age = (utcnow() - _dt.datetime.fromisoformat(r["heartbeat"])).total_seconds()
    return age < HEARTBEAT_STALE_SECONDS, r["pid"]


def acquire(con, pid):
    with db.tx(con):
        alive, other = worker_alive(con)
        if alive and other != pid:
            return False
        con.execute("INSERT OR REPLACE INTO worker(id, pid, heartbeat, started_at) VALUES (1,?,?,?)",
                    (pid, db.now(), db.now()))
    return True


def heartbeat(con, pid):
    con.execute("UPDATE worker SET heartbeat=? WHERE id=1 AND pid=?", (db.now(), pid))


def release(con, pid):
    con.execute("UPDATE worker SET heartbeat=NULL WHERE id=1 AND pid=?", (pid,))


# ---------------------------------------------------------------------------
# environment (real or fixture)
# ---------------------------------------------------------------------------
class Env:
    """Bundles providers so tests and the fixture demo can inject fakes."""

    def __init__(self, paths, cfg, client=None, demand_provider=None, image_checker=None, creative=None, sleep=time.sleep):
        self.paths, self.cfg = paths, cfg
        self.client, self.demand_provider = client, demand_provider
        self.image_checker = image_checker or verify.check_image
        self.creative = creative
        self.sleep = sleep


def build_env(paths, job_mode):
    cfg = load_config(paths)
    if job_mode == "fixture":
        from . import fixtures
        cfg["apify"]["poll"] = {"initial_seconds": 0, "max_seconds": 0}
        for k, v in fixtures.DEMO_ECONOMICS.items():  # labelled as fixture assumptions in the report
            if cfg["economics"].get(k) is None:
                cfg["economics"][k] = v
        return Env(paths, cfg, fixtures.FakeApify(paths.db), fixtures.FakeTrends(), fixtures.fake_image_check,
                   creative=fixtures.FixtureCreative())
    creds = load_credentials()
    client = apify.ApifyClient(creds.get("APIFY_TOKEN"), cfg["apify"]["actor_id"]) if creds.get("APIFY_TOKEN") else None
    from . import creative
    return Env(paths, cfg, client, demand.SerpApiTrends(creds.get("SERPAPI_API_KEY")),
               creative=creative.ClaudeApiCreative(creds.get("ANTHROPIC_API_KEY")) if creds.get("ANTHROPIC_API_KEY") else None)


# ---------------------------------------------------------------------------
# discovery plan
# ---------------------------------------------------------------------------
def discovery_plan(cfg, country):
    """Round-robin over categories so yield monitoring can rotate away from tired categories."""
    c = cfg["countries"][country]
    per_cat = []
    for cat in cfg["categories"]:
        items = []
        for lang in c["languages"]:
            for term in cat["terms"].get(lang, []):
                for src in c["sources"]:
                    if src.get("verified"):
                        items.append({"category_id": cat["id"], "lang": lang, "term": term, "source_id": src["id"]})
        per_cat.append(items)
    plan, i = [], 0
    while any(per_cat):
        for items in per_cat:
            if len(items) > i:
                plan.append(items[i])
        i += 1
        if all(len(items) <= i for items in per_cat):
            break
    return plan


def build_payload(cfg, source, term, n):
    mode = cfg["apify"].get("scrape_mode", "AUTO")
    if source["kind"] == "search_engine":
        return {"searchEngineKeyword": term, "countryCode": source["countryCode"],
                "scrapeModeSearchEngine": "Products", "maxSearchEngineProducts": n}
    return {"keyword": term, "marketplaces": [source["marketplace"]], "maxProductResults": n, "scrapeMode": mode}


def source_by_id(cfg, country, sid):
    if sid == cfg["supplier_source"]["id"]:
        return cfg["supplier_source"]
    for s in cfg["countries"][country]["sources"]:
        if s["id"] == sid:
            return s
    raise KeyError(sid)


# ---------------------------------------------------------------------------
# job processing
# ---------------------------------------------------------------------------
class JobRunner:
    def __init__(self, con, env, job_id):
        self.con, self.env, self.cfg = con, env, env.cfg
        self.job_id = job_id

    @property
    def job(self):
        return self.con.execute("SELECT * FROM jobs WHERE id=?", (self.job_id,)).fetchone()

    def checkpoint(self):
        return db.unj(self.job["checkpoint"], {}) or {}

    def save_checkpoint(self, cp):
        self.con.execute("UPDATE jobs SET checkpoint=?, updated_at=? WHERE id=?", (db.j(cp), db.now(), self.job_id))

    def set(self, **kw):
        cols = ", ".join("%s=?" % k for k in kw)
        self.con.execute("UPDATE jobs SET %s, updated_at=? WHERE id=?" % cols, (*kw.values(), db.now(), self.job_id))

    def log(self, msg, level="info"):
        db.log(self.con, self.job_id, msg, level)

    # -- schema ------------------------------------------------------------
    def schema(self):
        job = self.job
        if job["mode"] != "fixture" and self.env.client is None:
            raise apify.ApifyError("APIFY_TOKEN not configured")
        row = None
        if job["actor_build"]:
            b = json.loads(job["actor_build"])
            row = self.con.execute("SELECT schema FROM schema_cache WHERE actor_id=? AND build_id=?",
                                   (self.cfg["apify"]["actor_id"], b["build_id"])).fetchone()
            if row:
                return b, json.loads(row["schema"])
        info = self.env.client.latest_build()
        b = {"build_id": info["build_id"], "build_number": info["build_number"]}
        self.con.execute("INSERT OR REPLACE INTO schema_cache VALUES (?,?,?,?,?)",
                         (self.cfg["apify"]["actor_id"], b["build_id"], b["build_number"], json.dumps(info["schema"]), db.now()))
        self.set(actor_build=db.j(b))
        self.log("Using actor %s build %s (%s)" % (self.cfg["apify"]["actor_id"], b["build_number"], b["build_id"]))
        return b, info["schema"]

    # -- budget --------------------------------------------------------------
    def estimate(self, n_products):
        if self.job["mode"] == "fixture":
            return 0.0
        e = self.cfg["apify"]["estimated_cost"]
        return round(e["per_run_start_usd"] + e["per_product_usd"] * n_products, 4)

    def budget_allows(self, est):
        b = self.cfg["budget"]
        if self.job["mode"] == "fixture":
            cap = b.get("daily_spend_cap_usd") or 1.0
        else:
            cap = b.get("daily_spend_cap_usd")
            if cap is None:
                return False, "daily spend cap not configured"
        s = db.spend_today(self.con)
        spent = s["actual"] + s["estimate"]
        if spent + est > cap:
            return False, "daily spend cap reached (USD %.4f spent/committed of %.2f; next batch est. %.4f)" % (spent, cap, est)
        runs = self.con.execute("""SELECT COUNT(*) FROM batches b JOIN jobs j ON j.id=b.job_id WHERE j.mode=? AND
                                   b.apify_run_id IS NOT NULL AND substr(b.submit_attempt_at,1,10)=?""",
                                (self.job["mode"], db.today())).fetchone()[0]
        if runs >= b["daily_run_cap"]:
            return False, "daily run cap reached (%d runs)" % runs
        return True, ""

    def in_flight(self):
        return self.con.execute("SELECT COUNT(*) FROM batches WHERE job_id=? AND status IN ('submitting','submitted','running','uncertain')",
                                (self.job_id,)).fetchone()[0]

    # -- submit / poll / reconcile -----------------------------------------
    def create_batch(self, purpose, source_id, payload, est, category_id=None, term=None, lang=None, product_id=None):
        seq = self.con.execute("SELECT COALESCE(MAX(seq),0)+1 FROM batches WHERE job_id=?", (self.job_id,)).fetchone()[0]
        bid = "%s-B%04d" % (self.job_id, seq)
        self.con.execute("""INSERT INTO batches(id, job_id, seq, purpose, source_id, category_id, term, lang, product_id,
                            input_json, input_hash, status, est_cost, created_at, updated_at)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         (bid, self.job_id, seq, purpose, source_id, category_id, term, lang, product_id,
                          db.j(payload), apify.input_hash(payload), "planned", est, db.now(), db.now()))
        return bid

    def submit(self, bid):
        b = self.con.execute("SELECT * FROM batches WHERE id=?", (bid,)).fetchone()
        build, _ = self.schema()
        payload = json.loads(b["input_json"])
        # Persist intent BEFORE calling the paid API, so a crash leaves a reconcilable record.
        with db.tx(self.con):
            self.con.execute("UPDATE batches SET status='submitting', submit_attempt_at=?, attempts=attempts+1, actor_build=?, updated_at=? WHERE id=?",
                             (db.now(), build["build_number"], db.now(), bid))
            self.con.execute("INSERT INTO spend(day, job_id, batch_id, provider, amount, kind, ts) VALUES (?,?,?,?,?,?,?)",
                             (db.today(), self.job_id, bid, "apify", b["est_cost"], "estimate", db.now()))
        try:
            run = self.env.client.start_run(payload, build["build_number"], self.cfg["budget"].get("max_charge_per_run_usd"))
        except apify.UncertainSubmission as e:
            self.con.execute("UPDATE batches SET status='uncertain', error=?, next_poll_at=?, updated_at=? WHERE id=?",
                             (str(e), iso(utcnow() + _dt.timedelta(seconds=30)), db.now(), bid))
            self.log("Batch %s submission uncertain (%s); will reconcile before any retry" % (bid, e), "warn")
            return
        except apify.ApifyError as e:
            with db.tx(self.con):
                self.con.execute("UPDATE batches SET status='failed', error=?, updated_at=? WHERE id=?", (str(e), db.now(), bid))
                self.con.execute("DELETE FROM spend WHERE batch_id=? AND kind='estimate'", (bid,))
            self.log("Batch %s rejected by Apify: %s" % (bid, e), "error")
            return
        self.mark_submitted(bid, run)

    def mark_submitted(self, bid, run):
        self.con.execute("""UPDATE batches SET status='submitted', apify_run_id=?, dataset_id=?, poll_interval=?,
                            next_poll_at=?, error=NULL, updated_at=? WHERE id=?""",
                         (run["id"], run.get("defaultDatasetId"), self.cfg["apify"]["poll"]["initial_seconds"],
                          iso(utcnow() + _dt.timedelta(seconds=self.cfg["apify"]["poll"]["initial_seconds"])), db.now(), bid))
        self.log("Batch %s submitted as Apify run %s" % (bid, run["id"]))

    def reconcile(self, b):
        """Find a run that may have been created by a lost submit response before re-submitting."""
        payload = json.loads(b["input_json"])
        since = iso(_dt.datetime.fromisoformat(b["submit_attempt_at"]) - _dt.timedelta(seconds=120))
        try:
            run = self.env.client.find_run_for_input(payload, since)
        except apify.ApifyError as e:
            self.log("Reconcile of %s deferred: %s" % (b["id"], e), "warn")
            return
        if run:
            self.log("Reconciled %s: found existing run %s, no new charge" % (b["id"], run["id"]))
            self.mark_submitted(b["id"], run)
            return
        age = (utcnow() - _dt.datetime.fromisoformat(b["submit_attempt_at"])).total_seconds()
        if age >= RECONCILE_GRACE_SECONDS:
            with db.tx(self.con):
                self.con.execute("UPDATE batches SET status='planned', updated_at=? WHERE id=?", (db.now(), b["id"]))
                self.con.execute("DELETE FROM spend WHERE batch_id=? AND kind='estimate'", (b["id"],))
            self.log("No run found for %s after %ds; safe to resubmit" % (b["id"], age))
        else:
            self.con.execute("UPDATE batches SET next_poll_at=? WHERE id=?",
                             (iso(utcnow() + _dt.timedelta(seconds=30)), b["id"]))

    def poll(self):
        rows = self.con.execute("""SELECT * FROM batches WHERE job_id=? AND status IN ('submitting','submitted','running','uncertain')
                                   AND (next_poll_at IS NULL OR next_poll_at<=?)""", (self.job_id, db.now())).fetchall()
        for b in rows:
            if b["status"] in ("submitting", "uncertain"):
                self.reconcile(b)
                continue
            try:
                run = self.env.client.get_run(b["apify_run_id"])
            except apify.ApifyError as e:
                self.log("Poll %s failed: %s" % (b["id"], e), "warn")
                continue
            st = run.get("status")
            if st in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT", "TIMED_OUT"):
                amount, src = apify.run_cost(run)
                with db.tx(self.con):
                    if amount is not None:
                        self.con.execute("DELETE FROM spend WHERE batch_id=? AND kind='estimate'", (b["id"],))
                        self.con.execute("INSERT INTO spend(day, job_id, batch_id, provider, amount, kind, ts) VALUES (?,?,?,?,?,?,?)",
                                         (b["submit_attempt_at"][:10], self.job_id, b["id"], "apify", amount, "actual", db.now()))
                    self.con.execute("""UPDATE batches SET status=?, dataset_id=?, actual_cost=?, cost_source=?, items_total=?,
                                        error=?, updated_at=? WHERE id=?""",
                                     ("succeeded" if st == "SUCCEEDED" else "failed", run.get("defaultDatasetId") or b["dataset_id"],
                                      amount, src or "estimate", (run.get("stats") or {}).get("itemCount"),
                                      None if st == "SUCCEEDED" else "run %s" % st, db.now(), b["id"]))
                self.log("Batch %s run %s %s" % (b["id"], b["apify_run_id"], st))
            else:
                interval = min((b["poll_interval"] or 10) * 1.5, self.cfg["apify"]["poll"]["max_seconds"])
                self.con.execute("UPDATE batches SET status='running', poll_interval=?, next_poll_at=?, updated_at=? WHERE id=?",
                                 (interval, iso(utcnow() + _dt.timedelta(seconds=interval)), db.now(), b["id"]))

    # -- ingestion -----------------------------------------------------------
    def matcher(self):
        if getattr(self, "_matcher", None) is None:
            m = dedupe.Matcher()
            for p in self.con.execute("SELECT id, identity_key, specs FROM products WHERE country=?", (self.job["country"],)):
                m.add(p["id"], p["identity_key"], json.loads(p["specs"]))
            self._matcher = m
        return self._matcher

    def ingest(self, max_pages=None):
        pages = 0
        for b in self.con.execute("SELECT * FROM batches WHERE job_id=? AND status='succeeded' ORDER BY seq", (self.job_id,)).fetchall():
            while True:
                if max_pages is not None and pages >= max_pages:
                    return
                b = self.con.execute("SELECT * FROM batches WHERE id=?", (b["id"],)).fetchone()
                try:
                    items = self.env.client.dataset_items(b["dataset_id"], b["ingest_offset"], INGEST_PAGE)
                except apify.ApifyError as e:
                    self.log("Dataset read %s deferred: %s" % (b["id"], e), "warn")
                    return
                pages += 1
                if not items:
                    self.con.execute("UPDATE batches SET status='ingested', updated_at=? WHERE id=?", (db.now(), b["id"]))
                    self.log("Batch %s ingested (%d rows, %d new products)" % (b["id"], b["ingest_offset"], b["new_products"]))
                    break
                self.ingest_page(b, items)

    def ingest_page(self, b, items):
        """One transaction per page: rows, offers, products and the checkpoint move together."""
        country = self.job["country"]
        src = source_by_id(self.cfg, country, b["source_id"])
        rate = self.cfg["countries"][country].get("usd_rate")
        m = self.matcher()
        new_count = 0
        try:
            self.con.execute("BEGIN IMMEDIATE")
            for i, row in enumerate(items):
                idx = b["ingest_offset"] + i
                cur = self.con.execute("INSERT OR IGNORE INTO raw_rows VALUES (?,?,?,?)", (b["id"], idx, self.job_id, db.j(row)))
                if cur.rowcount == 0:
                    continue
                for k, off in enumerate(dedupe.normalize_rows(row, src["id"], src.get("local"), db.now())):
                    key_idx = idx * 100 + k
                    if not off["title"]:
                        continue
                    price_usd = usd(off["price"], off["currency"], rate, self.cfg)
                    if b["purpose"] == "supplier":
                        self.con.execute("""INSERT OR IGNORE INTO offers(batch_id,row_index,job_id,product_id,kind,source_id,is_local,url,seller,title,variant,
                                            price,currency,price_usd,image,description,features,pack_contents,identifiers,observed_at)
                                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                         (b["id"], key_idx, self.job_id, b["product_id"], "supplier", src["id"], 0, off["url"], off["seller"],
                                          off["title"], " ".join(off["variant"].values()), off["price"], off["currency"], price_usd,
                                          off["image"], off["description"], db.j(off["features"]), off["pack_contents"],
                                          db.j(off["identifiers"]), off["observed_at"]))
                        continue
                    new_count += self.place_offer(b, key_idx, off, price_usd, m)
            self.con.execute("UPDATE batches SET ingest_offset=ingest_offset+?, new_products=new_products+?, updated_at=? WHERE id=?",
                             (len(items), new_count, db.now(), b["id"]))
            self.con.execute("COMMIT")
        except Exception:
            self.con.execute("ROLLBACK")
            self._matcher = None  # in-memory index may contain rolled-back products
            raise

    def place_offer(self, b, key_idx, off, price_usd, m):
        country = self.job["country"]
        ident = dedupe.identity_key(off)
        fp = dedupe.fingerprint(off)
        rel, pid, score, why = m.match(ident, fp)
        new = 0
        if rel == "same":
            jp = self.con.execute("SELECT relation FROM job_products WHERE job_id=? AND product_id=?", (self.job_id, pid)).fetchone()
            if not jp:
                self.con.execute("INSERT INTO job_products VALUES (?,?,?,?,?)", (self.job_id, pid, "duplicate_prior", None, score))
            status = "duplicate"
        else:
            cand = pid
            pid = "P" + hashlib.sha1(("%s|%s|%s" % (country, ident or "", fp["hash"])).encode()).hexdigest()[:12]
            if self.con.execute("SELECT 1 FROM products WHERE id=?", (pid,)).fetchone():
                pid = pid + hashlib.sha1(off["url"].encode() if off["url"] else b"x").hexdigest()[:4]
            reason = "Found by '%s' (%s) on %s at %s %s" % (b["term"], b["lang"], b["source_id"],
                                                            off["price"] if off["price"] is not None else "?", off["currency"] or "")
            self.con.execute("""INSERT INTO products(id,country,first_job_id,identity_key,fingerprint,title,variant,brand,model,specs,
                                pack_count,image,source_url,discovery_reason,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                             (pid, country, self.job_id, ident, fp["hash"], off["title"], " ".join(off["variant"].values()),
                              off["brand"], off["model"], json.dumps(fp), fp["pack"], off["image"], off["url"], reason, db.now()))
            relation = "new" if rel == "new" else "uncertain"
            self.con.execute("INSERT INTO job_products VALUES (?,?,?,?,?)",
                             (self.job_id, pid, relation, None if rel == "new" else cand, score))
            m.add(pid, ident, fp)
            new = 1 if rel == "new" else 0
            status = relation
        self.con.execute("""INSERT OR IGNORE INTO offers(batch_id,row_index,job_id,product_id,kind,source_id,is_local,url,seller,title,variant,
                            price,currency,price_usd,image,description,features,pack_contents,identifiers,observed_at,match_status,match_score)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         (b["id"], key_idx, self.job_id, pid, "retail", off["source_id"], off["is_local"], off["url"], off["seller"],
                          off["title"], " ".join(off["variant"].values()), off["price"], off["currency"], price_usd, off["image"],
                          off["description"], db.j(off["features"]), off["pack_contents"], db.j(off["identifiers"]),
                          off["observed_at"], status, score))
        return new

    # -- discovery phase -----------------------------------------------------
    def new_count(self):
        return self.con.execute("SELECT COUNT(*) FROM job_products WHERE job_id=? AND relation='new'", (self.job_id,)).fetchone()[0]

    def dispatch_discovery(self):
        job = self.job
        cfg = self.cfg
        cp = self.checkpoint()
        plan = discovery_plan(cfg, job["country"])
        dcfg = cfg["discovery"]
        while self.in_flight() < cfg["budget"]["max_concurrent_runs"]:
            remaining = job["target"] - self.new_count()
            if remaining <= 0:
                return None
            idx = cp.get("plan_index", 0)
            # skip tired categories and inputs already run for this country in any job (fresh sources only)
            while idx < len(plan):
                item = plan[idx]
                if item["category_id"] in cp.get("rotated_categories", []) or item["source_id"] in cp.get("dead_sources", {}):
                    idx += 1
                    continue
                n = max(5, min(dcfg["products_per_batch"], remaining * 2))
                payload = build_payload(cfg, source_by_id(cfg, job["country"], item["source_id"]), item["term"], n)
                if self.con.execute("""SELECT 1 FROM batches b JOIN jobs j ON j.id=b.job_id WHERE j.country=? AND j.mode=?
                                       AND b.input_hash=? AND b.status NOT IN ('failed','planned')""",
                                    (job["country"], job["mode"], apify.input_hash(payload))).fetchone():
                    idx += 1
                    continue
                break
            if idx >= len(plan):
                cp["plan_index"] = idx
                self.save_checkpoint(cp)
                dead = cp.get("dead_sources", {})
                why = "sources_exhausted" + (" (no data from: %s)" % ", ".join(sorted(dead)) if dead else "")
                return why if self.in_flight() == 0 else None
            _, schema = self.schema()
            try:
                apify.validate_input(payload, schema)
            except apify.SchemaError as e:
                self.log("Skipping plan item %d (%s on %s): %s" % (idx, item["term"], item["source_id"], e), "warn")
                cp["plan_index"] = idx + 1
                self.save_checkpoint(cp)
                continue
            est = self.estimate(n)
            ok, why = self.budget_allows(est)
            if not ok:
                cp["plan_index"] = idx
                self.save_checkpoint(cp)
                return ("budget_exhausted: " + why) if self.in_flight() == 0 else None
            bid = self.create_batch("discovery", item["source_id"], payload, est, item["category_id"], item["term"], item["lang"])
            cp["plan_index"] = idx + 1
            self.save_checkpoint(cp)
            self.submit(bid)
        return None

    def mark_dead_sources(self):
        """A source whose last two finished batches returned zero rows is switched off for this job."""
        cp = self.checkpoint()
        dead = cp.get("dead_sources", {})
        srcs = {r[0] for r in self.con.execute("SELECT DISTINCT source_id FROM batches WHERE job_id=? AND purpose='discovery'", (self.job_id,))}
        changed = False
        for sid in srcs - set(dead):
            rows = self.con.execute("""SELECT ingest_offset, status FROM batches WHERE job_id=? AND source_id=? AND purpose='discovery'
                                       AND status IN ('ingested','failed') ORDER BY seq DESC LIMIT 2""", (self.job_id, sid)).fetchall()
            if len(rows) == 2 and all(r["ingest_offset"] == 0 for r in rows):
                dead[sid] = "returned no data in 2 consecutive runs (provider-side outage or blocked source)"
                self.log("Source %s switched off for this job: %s" % (sid, dead[sid]), "warn")
                changed = True
        if changed:
            cp["dead_sources"] = dead
            self.save_checkpoint(cp)

    def rotate_low_yield(self):
        """Stop spending on a category once its recent batches stop producing new products."""
        cp = self.checkpoint()
        d = self.cfg["discovery"]
        rotated = set(cp.get("rotated_categories", []))
        cats = {r["category_id"] for r in self.con.execute("SELECT DISTINCT category_id FROM batches WHERE job_id=? AND purpose='discovery'", (self.job_id,))}
        for cat in cats - rotated:
            rows = self.con.execute("""SELECT new_products, ingest_offset FROM batches WHERE job_id=? AND category_id=? AND status='ingested'
                                       ORDER BY seq DESC LIMIT ?""", (self.job_id, cat, d["low_yield_batches_before_rotate"])).fetchall()
            if len(rows) == d["low_yield_batches_before_rotate"] and all(
                    r["new_products"] < d["low_yield_threshold"] * max(1, r["ingest_offset"]) for r in rows):
                rotated.add(cat)
                self.log("New-product yield fell in category %s; rotating to fresh categories" % cat)
        if rotated != set(cp.get("rotated_categories", [])):
            cp["rotated_categories"] = sorted(rotated)
            self.save_checkpoint(cp)

    def step_discovery(self):
        self.poll()
        self.ingest()
        self.mark_dead_sources()
        self.rotate_low_yield()
        if self.new_count() >= self.job["target"]:
            if self.in_flight():
                return  # let paid runs finish and be ingested; never abandon a charged run
            self.log("Discovery target reached: %d new products" % self.new_count())
            self.set(phase="evidence")
            return
        stop = self.dispatch_discovery()
        if stop and self.in_flight() == 0 and not self.con.execute(
                "SELECT 1 FROM batches WHERE job_id=? AND status='succeeded'", (self.job_id,)).fetchone():
            cp = self.checkpoint()
            cp["discovery_stop"] = stop
            self.save_checkpoint(cp)
            self.set(stop_reason=stop, phase="evidence")
            self.log("Discovery stopped: %s" % stop, "warn")

    # -- evidence phase -----------------------------------------------------
    def shortlist(self):
        country = self.job["country"]
        min_price = self.cfg["countries"][country].get("min_selling_price_usd") or 0
        rows = self.con.execute("""SELECT p.id, p.title,
                     (SELECT COUNT(*) FROM offers o WHERE o.product_id=p.id AND o.kind='retail') n_offers,
                     (SELECT MAX(price_usd) FROM offers o WHERE o.product_id=p.id AND o.kind='retail' AND o.is_local=1) local_max
                   FROM products p JOIN job_products jp ON jp.product_id=p.id WHERE jp.job_id=? AND jp.relation='new'""",
                                (self.job_id,)).fetchall()
        eligible = [r for r in rows if r["local_max"] is not None and r["local_max"] >= min_price]
        eligible.sort(key=lambda r: (-r["n_offers"], -(r["local_max"] or 0)))
        return [r["id"] for r in eligible[: self.cfg["discovery"]["shortlist_size"]]]

    def step_evidence(self):
        cp = self.checkpoint()
        if "shortlist" not in cp:
            cp["shortlist"] = self.shortlist()
            self.save_checkpoint(cp)
            self.log("Shortlisted %d products for supplier, demand and image checks" % len(cp["shortlist"]))
        self.poll()
        self.ingest()
        sup = self.cfg["supplier_source"]
        for pid in cp["shortlist"]:
            if self.con.execute("SELECT 1 FROM batches WHERE job_id=? AND purpose='supplier' AND product_id=?", (self.job_id, pid)).fetchone():
                continue
            if pid in cp.get("supplier_skipped", {}):
                continue
            if self.in_flight() >= self.cfg["budget"]["max_concurrent_runs"]:
                break
            p = self.con.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
            term = supplier_query(p["title"])
            n = self.cfg["discovery"]["supplier_results_per_product"]
            payload = build_payload(self.cfg, sup, term, n)
            est = self.estimate(n)
            ok, why = self.budget_allows(est)
            if not ok:
                cp.setdefault("supplier_skipped", {})[pid] = "not run: " + why
                self.save_checkpoint(cp)
                continue
            try:
                apify.validate_input(payload, self.schema()[1])
            except apify.SchemaError as e:
                cp.setdefault("supplier_skipped", {})[pid] = "not run: payload invalid (%s)" % e
                self.save_checkpoint(cp)
                continue
            bid = self.create_batch("supplier", sup["id"], payload, est, term=term, lang="en", product_id=pid)
            self.submit(bid)
        pending = self.con.execute("""SELECT COUNT(*) FROM batches WHERE job_id=? AND purpose='supplier'
                                      AND status NOT IN ('ingested','failed','aborted')""", (self.job_id,)).fetchone()[0]
        todo = [pid for pid in cp["shortlist"] if not self.con.execute(
            "SELECT 1 FROM batches WHERE job_id=? AND purpose='supplier' AND product_id=?", (self.job_id, pid)).fetchone()
                and pid not in cp.get("supplier_skipped", {})]
        if pending == 0 and not todo:
            self.collect_demand_and_images(cp["shortlist"])
            self.set(phase="qualify")

    def collect_demand_and_images(self, shortlist):
        country = self.job["country"]
        geo = country
        dcfg = self.cfg["demand"]
        for pid in shortlist:
            if self.con.execute("SELECT 1 FROM evidence WHERE job_id=? AND product_id=? AND kind='demand'", (self.job_id, pid)).fetchone():
                continue
            b = self.con.execute("""SELECT b.term FROM offers o JOIN batches b ON b.id=o.batch_id WHERE o.product_id=? AND o.kind='retail'
                                    ORDER BY b.seq LIMIT 1""", (pid,)).fetchone()
            query = b["term"] if b else supplier_query(self.con.execute("SELECT title FROM products WHERE id=?", (pid,)).fetchone()[0])
            est = 0.0 if self.job["mode"] == "fixture" else dcfg["estimated_cost_per_query_usd"]
            ok, why = self.budget_allows(est) if self.job["mode"] != "fixture" else (True, "")
            if not ok:
                status, summary, cost, csrc = "unavailable", {"query": query, "geo": geo, "error": "not run: " + why}, 0.0, None
            else:
                status, summary, cost, csrc = demand.get_demand(self.con, self.env.demand_provider, query, geo, dcfg["timeframe"], self.job_id, est)
            self.con.execute("""INSERT INTO evidence(job_id,product_id,kind,provider,status,geo,value,source_url,observed_at,cost_usd,cost_source,note)
                                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                             (self.job_id, pid, "demand", demand.PROVIDER, status, geo, db.j(summary),
                              "https://trends.google.com/trends/explore?geo=%s&q=%s" % (geo, query) if status == "ok" else None,
                              db.now(), cost, csrc, "search-term level demand for the discovery term"))
            p = self.con.execute("SELECT image FROM products WHERE id=?", (pid,)).fetchone()
            ok_img, detail = self.env.image_checker(p["image"])
            self.con.execute("""INSERT INTO evidence(job_id,product_id,kind,provider,status,value,source_url,observed_at)
                                VALUES (?,?,?,?,?,?,?,?)""",
                             (self.job_id, pid, "image_check", "http", "ok" if ok_img else "failed", db.j({"detail": detail}), p["image"], db.now()))

    # -- qualify phase -------------------------------------------------------
    def supplier_for(self, p, cp):
        if p["id"] in cp.get("supplier_skipped", {}):
            return {"status": "not_run", "detail": cp["supplier_skipped"][p["id"]]}
        b = self.con.execute("SELECT status, error FROM batches WHERE job_id=? AND purpose='supplier' AND product_id=?", (self.job_id, p["id"])).fetchone()
        if not b:
            return {"status": "not_run", "detail": "not shortlisted"}
        if b["status"] == "failed":
            return {"status": "unavailable", "detail": b["error"]}
        best = None
        for o in self.con.execute("SELECT * FROM offers WHERE job_id=? AND product_id=? AND kind='supplier'", (self.job_id, p["id"])):
            ok, score, detail = verify.supplier_variant_match(dict(p), dict(o))
            if ok and o["price_usd"] is not None and (best is None or o["price_usd"] < best["price_usd"]):
                best = {"status": "ok", "price_usd": o["price_usd"], "url": o["url"], "score": score,
                        "detail": detail, "title": o["title"]}
        return best or {"status": "not_found", "detail": "no supplier listing matched specs and pack contents"}

    def step_qualify(self):
        job = self.job
        country_cfg = self.cfg["countries"][job["country"]]
        cp = self.checkpoint()
        shortlist = set(cp.get("shortlist", []))
        products = self.con.execute("""SELECT p.* FROM products p JOIN job_products jp ON jp.product_id=p.id
                                       WHERE jp.job_id=? AND jp.relation='new'""", (self.job_id,)).fetchall()
        for p in products:
            local = [dict(o) for o in self.con.execute("SELECT * FROM offers WHERE product_id=? AND kind='retail' AND is_local=1", (p["id"],))]
            if p["id"] not in shortlist:
                prices = [o["price_usd"] for o in local if o["price_usd"]]
                minp = country_cfg.get("min_selling_price_usd")
                if prices and minp is not None and max(prices) < minp:
                    status, reasons = "REJECTED", ["Price too low: highest local price USD %.2f below the USD %s minimum" % (max(prices), minp)]
                elif not prices:
                    status, reasons = "PENDING", ["No priced local listing observed in %s" % country_cfg["name"]]
                elif minp is None:
                    status, reasons = "PENDING", ["Minimum selling price for %s not configured" % country_cfg["name"]]
                else:
                    status, reasons = "PENDING", ["Not in this run's evidence shortlist (top %d); resume or raise shortlist_size" % self.cfg["discovery"]["shortlist_size"]]
                self.save_q(p["id"], status, reasons, {}, "")
                continue
            ev = self.con.execute("SELECT * FROM evidence WHERE job_id=? AND product_id=? AND kind='demand' ORDER BY id DESC", (self.job_id, p["id"])).fetchone()
            dem = {"status": ev["status"], "summary": json.loads(ev["value"])} if ev else {"status": "unavailable", "summary": {"error": "not collected"}}
            sup = self.supplier_for(p, cp)
            status, reasons, eco, unc = qualify.decide(dict(p), country_cfg, self.cfg, local, sup, dem)
            img = self.con.execute("SELECT * FROM evidence WHERE job_id=? AND product_id=? AND kind='image_check' ORDER BY id DESC", (self.job_id, p["id"])).fetchone()
            if img and img["status"] != "ok":
                d = json.loads(img["value"])["detail"]
                if status == "QUALIFIED":
                    status, reasons = "PENDING", ["Image does not load (%s); verify a working product image first" % d]
                else:
                    reasons = reasons + ["Image does not load (%s)" % d]
            eco["supplier"] = sup
            self.save_q(p["id"], status, reasons, eco, unc)
        self.set(phase="creative")

    def save_q(self, pid, status, reasons, eco, unc):
        self.con.execute("INSERT OR REPLACE INTO qualifications VALUES (?,?,?,?,?,?,?)",
                         (self.job_id, pid, status, db.j(reasons), db.j(eco), unc, db.now()))

    # -- creative + report -------------------------------------------------
    def step_creative(self):
        if self.env.creative is not None:
            from . import creative
            for q in self.con.execute("SELECT product_id FROM qualifications WHERE job_id=? AND status='QUALIFIED'", (self.job_id,)).fetchall():
                if self.con.execute("SELECT 1 FROM creatives WHERE job_id=? AND product_id=?", (self.job_id, q["product_id"])).fetchone():
                    continue
                ctx = creative.context(self.con, self.cfg, self.job_id, q["product_id"])
                try:
                    angles = creative.validate_angles(self.env.creative.generate(ctx))
                except Exception as e:  # noqa: BLE001 - creative failure never blocks the report
                    self.log("Creative generation for %s failed: %s" % (q["product_id"], e), "warn")
                    continue
                creative.save(self.con, self.job_id, q["product_id"], angles, self.env.creative.name)
        self.set(phase="report")

    def step_report(self):
        from . import report
        job = self.job
        counts = db.job_counts(self.con, self.job_id)
        complete = counts["new_products"] >= job["target"]
        stop = job["stop_reason"] or ("target reached" if complete else "unknown")
        status = "complete" if complete else "partial"
        if not complete:
            cp = self.checkpoint()
            cp["resume_hint"] = "Run '/leadsbounty resume %s' after raising the budget or on a new day" % self.job_id
            self.save_checkpoint(cp)
        self.set(status=status, stop_reason=stop)
        out = report.generate(self.con, self.cfg, self.env.paths, self.job_id)
        self.set(report_dir=out, phase="done")
        self.log("Report written to %s (%s)" % (out, status))

    def step(self):
        job = self.job
        if job["status"] == "queued":
            self.set(status="running")
        phase = job["phase"]
        try:
            if phase == "discovery":
                self.step_discovery()
            elif phase == "evidence":
                self.step_evidence()
            elif phase == "qualify":
                self.step_qualify()
            elif phase == "creative":
                self.step_creative()
            elif phase == "report":
                self.step_report()
        except apify.ApifyError as e:
            if "APIFY_TOKEN" in str(e):
                self.set(stop_reason="credentials_missing: APIFY_TOKEN not configured", phase="report")
                return
            self.log("Apify error: %s" % e, "error")


def runner_for(con, env, job_id):
    """Catalog jobs (the discovery catalog) and legacy qualification jobs share the queue and worker."""
    cp = con.execute("SELECT checkpoint FROM jobs WHERE id=?", (job_id,)).fetchone()[0]
    if (db.unj(cp, {}) or {}).get("params", {}).get("kind") == "catalog":
        from .catalog import CatalogRunner
        return CatalogRunner(con, env, job_id)
    return JobRunner(con, env, job_id)


def supplier_query(title):
    toks = [t for t in dedupe.TOKEN_RE.findall(title or "") if t.lower() not in dedupe.STOP]
    return " ".join(toks[:6])


def usd(price, currency, country_rate, cfg):
    if price is None:
        return None
    cur = (currency or "").upper()
    if cur in ("USD", "$", "US$"):
        return round(price, 2)
    rates = {"SAR": 0.2667, "AED": 0.2723, "OMR": 2.6, "EUR": 1.08, "MAD": cfg["countries"].get("MA", {}).get("usd_rate"),
             "CNY": 0.14, "LBP": 1 / 89500.0}
    r = rates.get(cur)
    if r is None and not cur:
        return None
    return round(price * r, 2) if r else None


# ---------------------------------------------------------------------------
# main loop
# ---------------------------------------------------------------------------
def run(project_dir, env_factory=build_env, max_ticks=None, tick_sleep=3.0):
    paths = Paths(project_dir)
    paths.ensure()
    con = db.connect(paths.db)
    pid = os.getpid()
    if not acquire(con, pid):
        print("Another worker is already running for this project.")
        return 0
    idle_since = None
    ticks = 0
    envs = {}
    try:
        while True:
            heartbeat(con, pid)
            jobs = con.execute("SELECT id, mode FROM jobs WHERE status IN ('queued','running') ORDER BY created_at").fetchall()
            if not jobs:
                idle_since = idle_since or time.time()
                if time.time() - idle_since > IDLE_EXIT_SECONDS or max_ticks is not None:
                    return 0
            else:
                idle_since = None
            for j in jobs:
                env = envs.get(j["mode"]) or envs.setdefault(j["mode"], env_factory(paths, j["mode"]))
                try:
                    runner_for(con, env, j["id"]).step()
                except Exception as e:  # noqa: BLE001 - keep the worker alive, record the failure
                    db.log(con, j["id"], "Worker error: %s\n%s" % (e, traceback.format_exc()), "error")
            ticks += 1
            if max_ticks is not None and ticks >= max_ticks:
                return 0
            fast = any(con.execute("SELECT phase FROM jobs WHERE id=?", (j["id"],)).fetchone()[0] != "discovery" for j in jobs)
            time.sleep(0.05 if fast or (jobs and jobs[0]["mode"] == "fixture") else tick_sleep)
    finally:
        release(con, pid)
        con.close()


if __name__ == "__main__":
    sys.exit(run(sys.argv[1]))
