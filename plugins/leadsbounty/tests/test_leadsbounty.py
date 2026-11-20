"""Leads Bounty Product Research test suite. Offline; no money is spent.
Run: python -m unittest discover -s plugins/leadsbounty/tests -v
"""
import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
sys.path.insert(0, PLUGIN)

from lb import apify, db, dedupe, demand, fixtures, qualify, verify, worker  # noqa: E402
from lb.config import Paths, load_config  # noqa: E402

LB = os.path.join(PLUGIN, "scripts", "lb.py")
PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00"
       b"\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")


def run_cli(args, cwd, env_extra=None):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    env.update(env_extra or {})
    r = subprocess.run([sys.executable] + args, cwd=cwd, capture_output=True, text=True, encoding="utf-8", env=env, timeout=120)
    return r.returncode, r.stdout + r.stderr


class TempProject(unittest.TestCase):
    def setUp(self):
        self.dir = os.path.join(tempfile.mkdtemp(prefix="lb-test-"), "My Project With Spaces")
        os.makedirs(os.path.join(self.dir, ".claude"))
        self.env = {"LEADSBOUNTY_CREDENTIALS": os.path.join(self.dir, "creds.env"), "LEADSBOUNTY_PROJECT": "",
                    "APIFY_TOKEN": "", "SERPAPI_API_KEY": "", "ANTHROPIC_API_KEY": ""}

    def tearDown(self):
        for _ in range(20):
            try:
                shutil.rmtree(self.dir)
                return
            except OSError:
                time.sleep(0.5)


# ---------------------------------------------------------------------------
class SetupTests(TempProject):
    def test_setup_twice_is_safe(self):
        rc1, out1 = run_cli([LB, "setup"], self.dir, self.env)
        skill = os.path.join(self.dir, ".claude", "skills", "leadsbounty", "SKILL.md")
        first = open(skill, encoding="utf-8").read()
        rc2, out2 = run_cli([LB, "setup"], self.dir, self.env)
        self.assertEqual((rc1, rc2), (0, 0), out1 + out2)
        self.assertEqual(first, open(skill, encoding="utf-8").read())
        self.assertIn("Kept existing .leadsbounty/config.json", out2)
        self.assertEqual(open(os.path.join(self.dir, ".gitignore")).read().count(".leadsbounty/"), 1)
        self.assertIn("Leads Bounty Product Research", out1)
        self.assertNotRegex(open(self.env["LEADSBOUNTY_CREDENTIALS"]).read(), r"APIFY_TOKEN=\S")

    def test_existing_unrelated_skill_is_preserved(self):
        d = os.path.join(self.dir, ".claude", "skills", "leadsbounty")
        os.makedirs(d)
        with open(os.path.join(d, "SKILL.md"), "w") as f:
            f.write("---\nname: leadsbounty\ndescription: my own thing\n---\nmine")
        rc, out = run_cli([LB, "setup"], self.dir, self.env)
        self.assertEqual(rc, 0)
        self.assertIn("CONFLICT", out)
        self.assertEqual(open(os.path.join(d, "SKILL.md")).read().splitlines()[-1], "mine")
        self.assertTrue(os.path.exists(os.path.join(self.dir, ".claude", "skills", "leadsbounty-research", "SKILL.md")))


class RoutingTests(TempProject):
    def test_both_spellings_reach_same_job_and_worker(self):
        run_cli([LB, "setup"], self.dir, self.env)
        env = dict(self.env, LEADSBOUNTY_NO_AUTOSTART="1")
        args = ["--period", "last 30 days", "--category", "automotive", "--fixture", "--start"]
        rc1, out1 = run_cli([LB, "scrap", "lebanon", "12"] + args + ["--via", "native"], self.dir, env)
        rc2, out2 = run_cli([os.path.join(".leadsbounty", "lb.py"), "scrap lebanon 12"] + args + ["--via", "project"], self.dir, env)
        self.assertEqual((rc1, rc2), (0, 0), out1 + out2)
        j1 = re.search(r"Queued job (\S+)", out1).group(1)
        self.assertIn("already running: %s" % j1, out2)
        self.assertIn("PLANNED SPLIT: 12 distinct products = 6 Top Sellers + 6 Newly Listed", out1)
        con = db.connect(Paths(self.dir).db)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 1)
        vias = [r[0] for r in con.execute("SELECT msg FROM events WHERE job_id=?", (j1,))]
        self.assertTrue(any("via native" in v for v in vias) and any("via project" in v for v in vias))
        con.close()
        # now let the project route start the one detached worker and finish the job
        rc, out = run_cli([os.path.join(".leadsbounty", "lb.py"), "resume", j1, "--via", "project"], self.dir, self.env)
        self.assertIn("Worker: started", out)
        deadline = time.time() + 90
        while time.time() < deadline:
            con = db.connect(Paths(self.dir).db)
            st = con.execute("SELECT status, phase FROM jobs WHERE id=?", (j1,)).fetchone()
            con.close()
            if st["phase"] == "done":
                break
            time.sleep(1)
        self.assertIn(st["status"], ("complete", "partial"))
        for f in ("report.html", "catalog.csv", "catalog.json"):
            self.assertTrue(os.path.exists(os.path.join(self.dir, ".leadsbounty", "reports", j1, f)), f)
        html = open(os.path.join(self.dir, ".leadsbounty", "reports", j1, "report.html"), encoding="utf-8").read()
        self.assertIn("FIXTURE DEMO", html)
        self.assertIn("Published by Leads Bounty LLC", html)
        self.assertFalse(os.path.exists(os.path.join(self.dir, ".leadsbounty", "reports", j1, "report.pdf")))  # PDF is opt-in
        rc, out = run_cli([LB, "report", j1, "--pdf"], self.dir, self.env)
        self.assertTrue(os.path.exists(os.path.join(self.dir, ".leadsbounty", "reports", j1, "report.pdf")), out)
        self.assertIn("leadsbounty.com", html)
        _, status = run_cli([LB, "status", j1], self.dir, self.env)
        self.assertIn("Newly Listed verified", status)

    def test_live_requires_credentials_and_budget(self):
        run_cli([LB, "setup"], self.dir, self.env)
        rc, out = run_cli([LB, "scrap", "lebanon", "500"], self.dir, self.env)
        self.assertEqual(rc, 4)
        self.assertIn("MISSING_FIELDS: period, category", out)
        rc, out = run_cli([LB, "scrap", "--country", "lebanon", "--total", "500", "--period", "last 30 days",
                           "--category", "automotive"], self.dir, self.env)
        self.assertEqual(rc, 3)
        self.assertIn("APIFY_TOKEN is missing", out)
        self.assertIn("daily spend cap", out)
        rc, out = run_cli([LB, "pilot", "lebanon", "--period", "last 7 days", "--category", "automotive"], self.dir,
                          dict(self.env, APIFY_TOKEN="dummy-not-used"))
        self.assertEqual(rc, 3)
        self.assertIn("Budget not confirmed", out)


# ---------------------------------------------------------------------------
class NoAccidentalSpendTests(TempProject):
    def test_preview_queues_nothing_and_setup_has_no_default_market(self):
        rc, out = run_cli([LB, "setup"], self.dir, self.env)
        self.assertNotIn("lebanon", out.lower())
        env = dict(self.env, APIFY_TOKEN="dummy-not-used")
        run_cli([LB, "config", "set", "budget.daily_spend_cap_usd", "5"], self.dir, env)
        rc, out = run_cli([LB, "scrap", "--country", "saudi", "--period", "current snapshot", "--category", "kitchen",
                           "--total", "10"], self.dir, env)
        self.assertEqual(rc, 0, out)
        self.assertIn("PREVIEW ONLY", out)
        self.assertIn("START_SEARCH", out)
        self.assertIn("Market: Saudi Arabia", out)
        self.assertIn("Period: Current snapshot", out)
        con = db.connect(Paths(self.dir).db)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 0)
        con.close()
        rc, out = run_cli([LB, "scrap"], self.dir, env)
        self.assertEqual(rc, 4)
        self.assertIn("MISSING_FIELDS: country, period, category, total", out)
        rc, out = run_cli([LB, "status"], self.dir, env)
        self.assertNotIn("Worker was not running", out)


class DedupeTests(unittest.TestCase):
    def off(self, title, url="", **ids):
        o = dedupe.normalize({"name": title, "url": url, "offers": {"price": "$30"}, **ids}, "s", 1, "t")
        return dedupe.identity_key(o), dedupe.fingerprint(o)

    def test_rules(self):
        m = dedupe.Matcher()
        m.add("A", *self.off("Electric Spin Scrubber Cordless 8 Brush Heads", "https://www.amazon.ae/dp/B0ABCDEFGH"))
        self.assertEqual(m.match(*self.off("Spin scrubber", "https://www.amazon.sa/dp/B0ABCDEFGH"))[0], "same")  # same ASIN
        self.assertEqual(m.match(*self.off("Electric Spin Scrubber Cordless 8 Brush Heads Lebanon"))[0], "same")
        self.assertEqual(m.match(*self.off("Electric Spin Scrubber Cordless 8 Brush Heads 2 Pack"))[0], "new")  # pack differs
        self.assertEqual(m.match(*self.off("Electric Spin Scrubber Cordless Brush Kitchen"))[0], "uncertain")
        m.add("B", *self.off("Power Bank 10000mAh Magnetic"))
        self.assertEqual(m.match(*self.off("Power Bank 20000mAh Magnetic"))[0], "new")  # spec differs

    def test_currency_and_seller_offers(self):
        rows = dedupe.normalize_rows({"product": {"title": "X Y Z"}, "sellers": [{"merchantName": "A", "price": "$12"},
                                                                                  {"merchantName": "B", "price": "SAR 40"}]}, "g", 1, "t")
        self.assertEqual([(r["seller"], r["price"], r["currency"]) for r in rows], [("A", 12.0, "USD"), ("B", 40.0, "SAR")])


class SchemaTests(unittest.TestCase):
    def test_payload_validation(self):
        schema = fixtures.FakeApify().latest_build()["schema"]
        cfg = load_config(Paths(tempfile.mkdtemp()))
        for src in cfg["countries"]["LB"]["sources"] + [cfg["supplier_source"]]:
            apify.validate_input(worker.build_payload(cfg, src, "test", 10), schema)
        with self.assertRaises(apify.SchemaError):
            apify.validate_input({"keyword": "x", "marketplaces": ["www.not-a-market.example"]}, schema)
        with self.assertRaises(apify.SchemaError):
            apify.validate_input({"keyword": "x", "madeUpField": 1}, schema)
        with self.assertRaises(apify.SchemaError):
            apify.validate_input({"maxProductResults": 0}, schema)


class EconomicsTests(unittest.TestCase):
    def test_breakeven(self):
        econ = dict(confirmation_rate=0.7, delivery_rate=0.75, delivery_fee_usd=4, return_cost_usd=3,
                    fulfillment_fee_usd=1.5, shipping_to_country_per_unit_usd=3, landed_cost_multiplier=1.0)
        e = qualify.economics(40, 8, econ)
        # landed 11; per delivered 40-11-1.5-4=23.5; failed 7; 0.7*(0.75*23.5-0.25*7)=11.1125
        self.assertAlmostEqual(e["breakeven_cpl"], 11.11, places=2)
        self.assertFalse(qualify.economics(40, 8, dict(econ, return_cost_usd=None))["complete"])


# ---------------------------------------------------------------------------
class ImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/ok.png":
                    self.send_response(200); self.send_header("Content-Type", "image/png"); self.end_headers(); self.wfile.write(PNG)
                elif self.path == "/page.html":
                    self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers(); self.wfile.write(b"<html>")
                else:
                    self.send_response(404); self.end_headers()

            def log_message(self, *a):
                pass
        cls.srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = "http://127.0.0.1:%d" % cls.srv.server_port

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def test_broken_images(self):
        self.assertTrue(verify.check_image(self.base + "/ok.png")[0])
        self.assertEqual(verify.check_image(self.base + "/missing.jpg"), (False, "HTTP 404"))
        self.assertFalse(verify.check_image(self.base + "/page.html")[0])
        self.assertFalse(verify.check_image(None)[0])

    def test_supplier_variant(self):
        p = {"title": "Magnetic Power Bank 10000mAh 15W", "variant": "", "pack_count": 1}
        self.assertTrue(verify.supplier_variant_match(p, {"title": "Magnetic Power Bank 10000mAh 15W wholesale"})[0])
        self.assertFalse(verify.supplier_variant_match(p, {"title": "Magnetic Power Bank 5000mAh 15W"})[0])
        self.assertFalse(verify.supplier_variant_match(p, {"title": "Magnetic Power Bank 10000mAh 15W 2 Pack"})[0])


# ---------------------------------------------------------------------------
class FlakyApify(fixtures.FakeApify):
    """Fixture data, live-like behaviour: costs money, can lose submit responses."""

    def __init__(self, lose_response=0, create_before_losing=True):
        super().__init__()
        self.lose_response = lose_response
        self.create_before_losing = create_before_losing
        self.created = []

    def start_run(self, payload, build_number=None, max_charge_usd=None):
        if self.lose_response:
            self.lose_response -= 1
            if self.create_before_losing:
                run = super().start_run(payload)
                self.created.append(run["id"])
            raise apify.UncertainSubmission("timed out")
        run = super().start_run(payload)
        self.created.append(run["id"])
        return run

    def get_run(self, run_id):
        r = super().get_run(run_id)
        r["usageTotalUsd"] = 0.02
        return r


class Unavailable:
    name = "serpapi_google_trends"

    def fetch(self, *a):
        raise demand.DemandUnavailable("SERPAPI_API_KEY not configured")


class WorkerTests(unittest.TestCase):
    def setUp(self):
        fixtures._RUNS.clear()
        self.dir = tempfile.mkdtemp(prefix="lb-w-")
        self.paths = Paths(self.dir)
        self.paths.ensure()
        self.con = db.connect(self.paths.db)
        self.cfg = load_config(self.paths)
        self.cfg["apify"]["poll"] = {"initial_seconds": 0, "max_seconds": 0}
        self.cfg["budget"].update(daily_spend_cap_usd=100, max_concurrent_runs=2)
        self.cfg["economics"].update(fixtures.DEMO_ECONOMICS)

    def tearDown(self):
        self.con.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def job(self, target=20, mode="live", jid="J1"):
        self.con.execute("INSERT INTO jobs(id,country,target,mode,status,phase,checkpoint,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                         (jid, "LB", target, mode, "running", "discovery", "{}", db.now(), db.now()))
        return jid

    def drive(self, env, jid, ticks=400):
        for _ in range(ticks):
            r = worker.JobRunner(self.con, env, jid)
            if r.job["phase"] == "done":
                break
            r.step()
        return self.con.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()

    def env(self, client, dem=None):
        return worker.Env(self.paths, self.cfg, client, dem or fixtures.FakeTrends(), fixtures.fake_image_check)

    def test_full_run_counts_and_no_double_counting(self):
        jid = self.job()
        job = self.drive(self.env(FlakyApify()), jid)
        c = db.job_counts(self.con, jid)
        self.assertEqual(job["status"], "complete")
        self.assertGreaterEqual(c["new_products"], 20)
        self.assertGreater(c["duplicates"], 0)
        self.assertEqual(c["retail_offers"], c["new_products"] + c["uncertain_matches"] + c["duplicates"])
        self.assertGreater(c["ready_to_test"], 0)
        # every evaluated product has a reason
        for q in self.con.execute("SELECT reasons FROM qualifications WHERE job_id=?", (jid,)):
            self.assertTrue(json.loads(q[0]))
        # second job on another day must not recount the same products
        self.con.execute("UPDATE jobs SET created_at='2000-01-01' WHERE id=?", (jid,))
        j2 = self.job(target=5, jid="J2")
        self.drive(self.env(FlakyApify()), j2)
        new2 = {r[0] for r in self.con.execute("SELECT product_id FROM job_products WHERE job_id='J2' AND relation='new'")}
        new1 = {r[0] for r in self.con.execute("SELECT product_id FROM job_products WHERE job_id='J1' AND relation='new'")}
        self.assertFalse(new1 & new2)

    def test_interrupted_ingestion_resumes_without_duplicates(self):
        jid = self.job(target=8)
        env = self.env(FlakyApify())
        r = worker.JobRunner(self.con, env, jid)
        r.dispatch_discovery()
        r.poll()
        orig = dedupe.normalize_rows
        calls = {"n": 0}

        def boom(*a, **k):
            calls["n"] += 1
            if calls["n"] == 3:
                raise RuntimeError("power cut")
            return orig(*a, **k)
        worker.dedupe.normalize_rows = boom
        try:
            with self.assertRaises(RuntimeError):
                r.ingest()
        finally:
            worker.dedupe.normalize_rows = orig
        offs = self.con.execute("SELECT SUM(ingest_offset) FROM batches").fetchone()[0]
        self.assertEqual(offs, 0)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM raw_rows").fetchone()[0], 0)
        worker.JobRunner(self.con, env, jid).ingest()
        rows = self.con.execute("SELECT COUNT(*) FROM raw_rows").fetchone()[0]
        expected = sum(len(fixtures._items_for(json.loads(b[0]))) for b in self.con.execute("SELECT input_json FROM batches"))
        self.assertEqual(rows, expected)
        # replaying a page is a no-op
        b = self.con.execute("SELECT * FROM batches LIMIT 1").fetchone()
        before = db.job_counts(self.con, jid)
        self.con.execute("UPDATE batches SET ingest_offset=0, status='succeeded' WHERE id=?", (b["id"],))
        worker.JobRunner(self.con, env, jid).ingest()
        self.assertEqual(before, db.job_counts(self.con, jid))

    def test_uncertain_submission_reconciles_without_second_charge(self):
        jid = self.job(target=3)
        client = FlakyApify(lose_response=1, create_before_losing=True)
        r = worker.JobRunner(self.con, self.env(client), jid)
        r.dispatch_discovery()
        st = [x[0] for x in self.con.execute("SELECT status FROM batches ORDER BY seq")]
        self.assertEqual(st[0], "uncertain")
        self.con.execute("UPDATE batches SET next_poll_at=NULL")
        r.poll()
        self.assertEqual(self.con.execute("SELECT status FROM batches ORDER BY seq").fetchone()[0], "submitted")
        self.assertEqual(len(client.created), len(set(client.created)))
        runs = self.con.execute("SELECT apify_run_id FROM batches WHERE apify_run_id IS NOT NULL").fetchall()
        self.assertEqual(len(runs), len({x[0] for x in runs}))

    def test_lost_submission_is_retried_once_after_grace(self):
        jid = self.job(target=3)
        client = FlakyApify(lose_response=1, create_before_losing=False)
        old = worker.RECONCILE_GRACE_SECONDS
        worker.RECONCILE_GRACE_SECONDS = 0
        try:
            r = worker.JobRunner(self.con, self.env(client), jid)
            r.dispatch_discovery()
            self.con.execute("UPDATE batches SET next_poll_at=NULL")
            r.poll()
            self.assertEqual(self.con.execute("SELECT status FROM batches ORDER BY seq").fetchone()[0], "planned")
            self.assertEqual(self.con.execute("SELECT COUNT(*) FROM spend WHERE batch_id=(SELECT id FROM batches ORDER BY seq LIMIT 1)").fetchone()[0], 0)
        finally:
            worker.RECONCILE_GRACE_SECONDS = old

    def test_budget_stop_is_partial_and_resumable(self):
        self.cfg["budget"]["daily_spend_cap_usd"] = 0.5   # each batch ~0.24 estimated, 0.02 actual
        self.cfg["budget"]["max_concurrent_runs"] = 1
        self.cfg["discovery"]["products_per_batch"] = 40
        jid = self.job(target=500)
        job = self.drive(self.env(FlakyApify()), jid)
        self.assertEqual(job["status"], "partial")
        self.assertIn("budget_exhausted", job["stop_reason"])
        self.assertIn("plan_index", json.loads(job["checkpoint"]))
        spent = db.spend_today(self.con)
        self.assertLessEqual(spent["actual"] + spent["estimate"], 0.5)
        html = open(os.path.join(job["report_dir"], "report.html"), encoding="utf-8").read()
        self.assertIn("PARTIAL", html)
        self.assertIn("resume", html)

    def test_unavailable_evidence_keeps_pending(self):
        jid = self.job(target=6)
        job = self.drive(self.env(FlakyApify(), Unavailable()), jid)
        self.assertEqual(job["status"], "complete")
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM qualifications WHERE job_id=? AND status='QUALIFIED'", (jid,)).fetchone()[0], 0)
        ev = self.con.execute("SELECT status, value FROM evidence WHERE kind='demand'").fetchall()
        self.assertTrue(ev and all(e[0] == "unavailable" for e in ev))
        reasons = " ".join(r[0] for r in self.con.execute("SELECT reasons FROM qualifications WHERE status='PENDING'"))
        self.assertIn("Demand evidence unavailable", reasons)

    def test_broken_image_never_qualifies(self):
        self.cfg["discovery"]["shortlist_size"] = 50
        jid = self.job(target=36)
        self.drive(self.env(FlakyApify()), jid)
        row = self.con.execute("""SELECT q.status, q.reasons FROM qualifications q JOIN products p ON p.id=q.product_id
                                  WHERE p.title LIKE 'Window Cleaning Robot%'""").fetchone()
        self.assertIsNotNone(row)
        self.assertNotEqual(row["status"], "QUALIFIED")
        self.assertIn("Image does not load", row["reasons"])


class DeadGoogle(FlakyApify):
    """Google search engine returns nothing (actor reports 'Domain under maintenance')."""

    def dataset_items(self, dataset_id, offset, limit=200):
        p = self._load(dataset_id)
        if p and "searchEngineKeyword" in p:
            return []
        return super().dataset_items(dataset_id, offset, limit)


class DeadSourceTests(WorkerTests):
    # only run the dead-source test here, not the inherited WorkerTests again
    test_full_run_counts_and_no_double_counting = test_interrupted_ingestion_resumes_without_duplicates = None
    test_uncertain_submission_reconciles_without_second_charge = test_lost_submission_is_retried_once_after_grace = None
    test_budget_stop_is_partial_and_resumable = test_unavailable_evidence_keeps_pending = test_broken_image_never_qualifies = None

    def test_fixture_runs_do_not_use_live_run_cap(self):
        self.cfg["budget"]["daily_run_cap"] = 3
        fx = self.job(target=6, mode="fixture", jid="FX")
        self.drive(self.env(FlakyApify()), fx)
        live = self.job(target=3, jid="LIVE")
        ok, why = worker.JobRunner(self.con, self.env(FlakyApify()), live).budget_allows(0.01)
        self.assertTrue(ok, why)

    def test_empty_source_is_switched_off(self):
        self.cfg["budget"]["max_concurrent_runs"] = 1
        jid = self.job(target=10)
        self.drive(self.env(DeadGoogle()), jid)
        cp = json.loads(self.con.execute("SELECT checkpoint FROM jobs WHERE id=?", (jid,)).fetchone()[0])
        self.assertIn("lb-google", cp.get("dead_sources", {}))
        n = self.con.execute("SELECT COUNT(*) FROM batches WHERE job_id=? AND source_id='lb-google'", (jid,)).fetchone()[0]
        self.assertLessEqual(n, 3)


class CatalogTests(unittest.TestCase):
    def setUp(self):
        fixtures._RUNS.clear()
        self.dir = tempfile.mkdtemp(prefix="lb-c-")
        self.paths = Paths(self.dir)
        self.paths.ensure()
        self.con = db.connect(self.paths.db)
        self.cfg = load_config(self.paths)
        self.cfg["apify"]["poll"] = {"initial_seconds": 0, "max_seconds": 0}
        self.cfg["budget"].update(daily_spend_cap_usd=100, max_concurrent_runs=2)

    def tearDown(self):
        self.con.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def run_job(self, total, period="last 30 days", category="automotive", target="LB", jid="C1"):
        from lb import catalog
        params = {"kind": "catalog", "target": target, "country_text": target, "period_text": period,
                  "category_text": category, "total": total, "requested_on": db.today()}
        self.con.execute("INSERT INTO jobs(id,country,target,mode,status,phase,checkpoint,created_at,updated_at) "
                         "VALUES (?,?,?,?,?,?,?,?,?)", (jid, target, total, "fixture", "running", "discovery",
                                                        db.j({"params": params}), db.now(), db.now()))
        env = worker.Env(self.paths, self.cfg, fixtures.FakeApify(), fixtures.FakeTrends(), fixtures.fake_image_check)
        for _ in range(300):
            r = worker.runner_for(self.con, env, jid)
            if r.job["phase"] == "done":
                break
            r.step()
        job = self.con.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        sel = catalog.select(catalog.classify(self.con, jid, catalog.parse_period(period)), total)
        return job, sel, json.loads(job["checkpoint"])

    def test_split(self):
        from lb.catalog import split_total
        self.assertEqual(split_total(10), {"top": 5, "new": 5})
        self.assertEqual(split_total(500), {"top": 250, "new": 250})
        self.assertEqual(split_total(11), {"top": 6, "new": 5})
        self.assertEqual(split_total(1), {"top": 1, "new": 0})

    def test_exact_count_and_extras_saved(self):
        job, sel, cp = self.run_job(4)
        self.assertLessEqual(len(sel["groups"]["top"]), 2)
        self.assertEqual(len(sel["groups"]["new"]), 2)
        self.assertGreater(len(sel["extras"]), 0)
        doc = json.load(open(os.path.join(job["report_dir"], "catalog.json"), encoding="utf-8"))
        self.assertEqual(len(doc["products"]), len(catalog_classify(self.con, "C1")))
        self.assertTrue(all(p["raw_records"] for p in doc["products"]))
        html = open(os.path.join(job["report_dir"], "report.html"), encoding="utf-8").read()
        self.assertLess(html.index("Products"), html.index("Scrape Report"))
        self.assertIn("FIXTURE DEMO", html)

    def test_current_only_bestseller_not_verified_for_30_days(self):
        job, sel, cp = self.run_job(4)
        self.assertEqual(len(sel["groups"]["top"]), 0)
        self.assertGreater(len(sel["current_bestsellers"]), 0)
        self.assertEqual(job["status"], "partial")
        self.assertIn("current ranking", job["stop_reason"])
        job2, sel2, _ = self.run_job(4, period="today", jid="C2")
        self.assertEqual(len(sel2["groups"]["top"]), 2)

    def test_unknown_listing_dates(self):
        job, sel, cp = self.run_job(6)
        self.assertGreater(len(sel["new_undated"]), 0)
        for p in sel["new_undated"]:
            self.assertIsNone(p["listing"])
        for p in sel["groups"]["new"]:
            self.assertTrue(p["listing"]["listing_date_raw"])
            self.assertIn("product page field", p["listing"]["listing_date_source"])

    def test_duplicates_merge_and_keep_badges(self):
        job, sel, cp = self.run_job(6, target="WW")
        allp = catalog_classify(self.con, "C1")
        self.assertTrue([p for p in allp.values() if len(p["sources"]) > 1], "same ASIN on several storefronts = one product")
        self.assertTrue([p for p in allp.values() if p["best_rank"] and p["new_release_rank"]], "both badges kept")
        ids = [p["product_id"] for g in sel["groups"].values() for p in g]
        self.assertEqual(len(ids), len(set(ids)))

    def test_specialist_fallback_and_both_primaries(self):
        job, sel, cp = self.run_job(4)
        cov = cp["coverage"]
        self.assertEqual(cov["www.autozone.com:specialist_search"]["rows"], 0)
        self.assertIn("www.autozone.com:specialist_search", cp["dead_sources"])
        self.assertGreater(cov["www.alibaba.com:category_search"]["rows"], 0)
        self.assertGreater(cov["www.amazon.ae:bestseller"]["rows"], 0)
        self.assertEqual(job["phase"], "done")

    def test_no_alibaba_spend_after_first_pass_when_it_cannot_help(self):
        job, sel, cp = self.run_job(40)  # more Newly Listed than the fixture can verify
        ali = [t for t in cp["tasks_done"] if t.startswith("ali-")]
        self.assertNotIn("ali-automotive-p2", ali)  # no further Alibaba category pages
        self.assertLessEqual(len([t for t in ali if t.startswith("ali-q-")]), 2)  # small supplier-query allowance only
        self.assertEqual(job["status"], "partial")
        self.assertIn("sources_exhausted", job["stop_reason"])

    def test_structured_seller_and_poison_row_do_not_stall(self):
        orig = fixtures.catalog_items_for

        def odd(payload):
            rows = orig(payload)
            if rows:
                rows[0]["seller"] = {"name": "Structured Seller LLC", "url": "https://example.invalid"}
                rows.insert(1, {"url": "https://www.amazon.ae/dp/B0POISON01", "name": "Poison", "offers": "not-a-dict",
                                "additionalProperties": {"extraProperties": "garbage"}})
            return rows
        fixtures.catalog_items_for = odd
        try:
            job, sel, cp = self.run_job(4)
        finally:
            fixtures.catalog_items_for = orig
        self.assertEqual(job["phase"], "done")
        sellers = [r[0] for r in self.con.execute("SELECT seller FROM offers WHERE seller LIKE 'Structured%'")]
        self.assertTrue(sellers)

    def test_current_snapshot_counts_current_bestsellers(self):
        job, sel, cp = self.run_job(4, period="current snapshot")
        self.assertEqual(len(sel["groups"]["top"]), 2)
        self.assertEqual(len(sel["groups"]["new"]), 2)
        for p in sel["groups"]["new"]:
            self.assertTrue(p["listing"]["listing_date"])

    def test_dashboard_contents(self):
        from lb import dashboard
        job, sel, cp = self.run_job(4)
        d = dashboard.build_data(self.con, self.cfg, "C1")
        self.assertEqual(d["request"]["market"], "Lebanon")
        self.assertEqual(d["request"]["storefronts"][0]["name"], "Amazon UAE")
        P = d["products"]
        main = [p for p in P if not p["dup"]]
        self.assertGreater(len(main), 4)  # every distinct product, beyond the target
        for p in main:  # Unmatched = neither group; a product may be in both groups
            self.assertEqual(p["unmatched"], not p["top"] and not p["new"])
        texts = [e["text"] for p in P for e in p["ev"]]
        self.assertTrue(any(re.match(r"#\d+ in .+ · observed", t) for t in texts))
        self.assertTrue(any(t.startswith("First available ") for t in texts))
        self.assertTrue(any(t == "New Releases badge · date unknown" for t in texts))
        cur = [e for p in P for e in p["ev"] if e["kind"] == "top" and e["text"].startswith("#")]
        self.assertTrue(cur and not any(e["match"] for e in cur))  # current ranks never match a 30-day range
        for p in P:
            self.assertIn(p["mk"], ("Amazon", "Alibaba", "AutoZone"))
            self.assertTrue(p["storefront"])
            self.assertTrue(p["found_by"])
        H = d["history"]
        self.assertEqual(H["jobs"][0]["id"], "C1")
        html = open(os.path.join(job["report_dir"], "report.html"), encoding="utf-8").read()
        for needle in ("All Discovered", "Newly Listed", "Top Selling", "Unmatched", "Scrape Report", "Matches selected period",
                       "catalog.csv", "catalog.json", "Price: low → high", "Price: high → low", "Include possible duplicates"):
            self.assertIn(needle, html)
        self.assertNotIn("</script><script", html.split('id="lb-data"')[1].split("</script>")[0])

    def test_sales_window_and_planner(self):
        from lb import catalog, queries
        import datetime as dt
        per30 = catalog.parse_period("last 30 days")
        per7 = catalog.parse_period("last 7 days")
        s = {"text": "400+ bought in past month", "window_days": 30, "observed_at": db.now()}
        self.assertTrue(catalog.sales_window_fits(s, per30))
        self.assertFalse(catalog.sales_window_fits(s, per7))
        ev = catalog.extract({"name": "X", "url": "https://www.amazon.sa/dp/B000000001", "offers": {"price": 10, "priceCurrency": "SAR"},
                              "reviewCount": 42, "additionalProperties": {"stars": 4.3, "monthlyPurchaseVolume": "100+ bought in past month",
                              "bestsellerRanks": [{"category": "Health", "rank": 8, "url": "u"}],
                              "attributes": [{"key": "Date First Available", "value": "4 November 2026"}]}},
                             {"id": "t", "source": "amazon", "marketplace": "www.amazon.sa", "region": "SA", "relation": "local",
                              "list_type": "category_search", "category": "health", "keyword": "foot massager"}, db.now())
        self.assertEqual((ev["rating"], ev["reviews"], ev["sales"]["min_units"], ev["page_ranks"][0]["rank"], ev["listing_date"]),
                         (4.3, 42, 100, 8, "2026-11-04"))
        self.assertEqual(ev["found_by"]["query"], "foot massager")
        fronts = [("www.amazon.sa", "SA", "local")]
        sd = queries.seeds("health", "SA", fronts)
        kws = {t["keyword"] for t in sd}
        self.assertTrue({"جهاز مساج للقدمين", "علبة تنظيم الحبوب", "دعامة رسغ قابلة للتعديل", "foot massager"} <= kws)
        self.assertTrue(any(t["source"] == "alibaba" and "OEM" in t["keyword"] or "wholesale" in t["keyword"] for t in sd))
        self.assertFalse(queries.relevant("health", "Commercial Equipment & Machinery > Commercial Robots"))
        self.assertTrue(queries.relevant("health", "Health & Medical > Rehabilitation Therapy Supplies"))
        ran = {queries.equivalent_key("www.amazon.sa", "category_search", "Foot  Massager", 40)}
        ordered, tired = queries.order(sd, {}, set(), ran, 40)
        self.assertNotIn("foot massager", [t["keyword"] for t in ordered if t["marketplace"] == "www.amazon.sa"])
        self.assertNotEqual(ordered[0]["subcat"], ordered[1]["subcat"])  # variety: round-robin across subcategories

    def test_listing_date_parsing(self):
        from lb.catalog import parse_listing_date, parse_period
        import datetime as dt
        self.assertEqual(parse_listing_date("\u200e 2026 \u0646\u0648\u0641\u0645\u0628\u0631 6"), dt.date(2026, 11, 6))
        self.assertEqual(parse_listing_date("November 6, 2026"), dt.date(2026, 11, 6))
        self.assertEqual(parse_listing_date("6 Nov. 2026"), dt.date(2026, 11, 6))
        self.assertIsNone(parse_listing_date("unknown"))
        p = parse_period("2026-10-01 to 2026-10-31")
        self.assertEqual((p["start"], p["end"], p["days"]), (dt.date(2026, 10, 1), dt.date(2026, 10, 31), 31))


def catalog_classify(con, jid):
    from lb import catalog
    return catalog.classify(con, jid, catalog.parse_period("last 30 days"))


class PackageHygieneTests(unittest.TestCase):
    def test_no_secrets_or_private_data(self):
        bad = re.compile(r"apify_api_[A-Za-z0-9]{10,}|sk-ant-[A-Za-z0-9-]{10,}|[a-f0-9]{64}(?![a-f0-9])")
        for root, dirs, files in os.walk(PLUGIN):
            dirs[:] = [d for d in dirs if d not in ("__pycache__",)]
            for f in files:
                p = os.path.join(root, f)
                self.assertFalse(f.endswith((".sqlite", ".env")) and "example" not in f, p)
                if f.endswith((".py", ".md", ".json", ".env")):
                    self.assertIsNone(bad.search(open(p, encoding="utf-8").read()), p)

    def test_manifests(self):
        repo = os.path.dirname(os.path.dirname(PLUGIN))
        pj = json.load(open(os.path.join(PLUGIN, ".claude-plugin", "plugin.json"), encoding="utf-8"))
        from lb import __version__
        self.assertEqual(pj["name"], "leadsbounty")
        self.assertEqual(pj["version"], __version__)
        mpath = os.path.join(repo, ".claude-plugin", "marketplace.json")
        if os.path.exists(mpath):  # repository / release layout; an installed plugin copy has no marketplace file
            mk = json.load(open(mpath, encoding="utf-8"))
            self.assertEqual(mk["name"], "leadsbounty-community")
            self.assertEqual(mk["plugins"][0]["source"], "./plugins/leadsbounty")
            self.assertEqual(mk["plugins"][0]["version"], __version__)
        for s in ("setup", "scrap", "status", "resume", "report"):
            self.assertTrue(os.path.exists(os.path.join(PLUGIN, "skills", s, "SKILL.md")))


if __name__ == "__main__":
    unittest.main()
