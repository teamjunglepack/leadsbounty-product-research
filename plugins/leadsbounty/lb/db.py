"""SQLite job queue and research database (one file per member project)."""
import datetime as _dt
import json
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, country TEXT NOT NULL, target INTEGER NOT NULL,
  mode TEXT NOT NULL DEFAULT 'live', status TEXT NOT NULL DEFAULT 'queued',
  phase TEXT NOT NULL DEFAULT 'discovery', stop_reason TEXT, checkpoint TEXT,
  actor_build TEXT, report_dir TEXT, created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS batches (
  id TEXT PRIMARY KEY, job_id TEXT NOT NULL, seq INTEGER NOT NULL, purpose TEXT NOT NULL,
  source_id TEXT, category_id TEXT, term TEXT, lang TEXT, product_id TEXT,
  input_json TEXT NOT NULL, input_hash TEXT NOT NULL, status TEXT NOT NULL,
  apify_run_id TEXT, dataset_id TEXT, actor_build TEXT, submit_attempt_at TEXT,
  est_cost REAL, actual_cost REAL, cost_source TEXT, ingest_offset INTEGER NOT NULL DEFAULT 0,
  items_total INTEGER, new_products INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
  next_poll_at TEXT, poll_interval REAL, error TEXT, created_at TEXT, updated_at TEXT,
  UNIQUE(job_id, seq)
);
CREATE TABLE IF NOT EXISTS raw_rows (
  batch_id TEXT NOT NULL, row_index INTEGER NOT NULL, job_id TEXT NOT NULL, payload TEXT NOT NULL,
  PRIMARY KEY (batch_id, row_index)
);
CREATE TABLE IF NOT EXISTS products (
  id TEXT PRIMARY KEY, country TEXT NOT NULL, first_job_id TEXT NOT NULL,
  identity_key TEXT, fingerprint TEXT NOT NULL, title TEXT, variant TEXT, brand TEXT, model TEXT,
  specs TEXT, pack_count INTEGER, image TEXT, source_url TEXT, discovery_reason TEXT, created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_products_identity ON products(country, identity_key);
CREATE TABLE IF NOT EXISTS offers (
  batch_id TEXT NOT NULL, row_index INTEGER NOT NULL, job_id TEXT NOT NULL, product_id TEXT,
  kind TEXT NOT NULL, source_id TEXT, is_local INTEGER, url TEXT, seller TEXT, title TEXT, variant TEXT,
  price REAL, currency TEXT, price_usd REAL, image TEXT, description TEXT, features TEXT,
  pack_contents TEXT, identifiers TEXT, observed_at TEXT, match_status TEXT, match_score REAL,
  PRIMARY KEY (batch_id, row_index)
);
CREATE TABLE IF NOT EXISTS job_products (
  job_id TEXT NOT NULL, product_id TEXT NOT NULL, relation TEXT NOT NULL,
  uncertain_of TEXT, score REAL, PRIMARY KEY (job_id, product_id)
);
CREATE TABLE IF NOT EXISTS evidence (
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, product_id TEXT, kind TEXT NOT NULL,
  provider TEXT, status TEXT NOT NULL, geo TEXT, value TEXT, source_url TEXT,
  observed_at TEXT, cost_usd REAL, cost_source TEXT, note TEXT
);
CREATE TABLE IF NOT EXISTS qualifications (
  job_id TEXT NOT NULL, product_id TEXT NOT NULL, status TEXT NOT NULL, reasons TEXT,
  economics TEXT, uncertainty TEXT, updated_at TEXT, PRIMARY KEY (job_id, product_id)
);
CREATE TABLE IF NOT EXISTS creatives (
  job_id TEXT NOT NULL, product_id TEXT NOT NULL, angles TEXT NOT NULL, generator TEXT,
  created_at TEXT, PRIMARY KEY (job_id, product_id)
);
CREATE TABLE IF NOT EXISTS spend (
  id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, job_id TEXT, batch_id TEXT,
  provider TEXT NOT NULL, amount REAL NOT NULL, kind TEXT NOT NULL, ts TEXT
);
CREATE TABLE IF NOT EXISTS demand_cache (
  provider TEXT, query TEXT, geo TEXT, timeframe TEXT, response TEXT, fetched_at TEXT,
  PRIMARY KEY (provider, query, geo, timeframe)
);
CREATE TABLE IF NOT EXISTS schema_cache (
  actor_id TEXT, build_id TEXT, build_number TEXT, schema TEXT, fetched_at TEXT,
  PRIMARY KEY (actor_id, build_id)
);
CREATE TABLE IF NOT EXISTS catalog_evidence (
  batch_id TEXT NOT NULL, row_index INTEGER NOT NULL, job_id TEXT NOT NULL, product_id TEXT, evidence TEXT NOT NULL,
  PRIMARY KEY (batch_id, row_index)
);
CREATE TABLE IF NOT EXISTS worker (id INTEGER PRIMARY KEY CHECK (id = 1), pid INTEGER, heartbeat TEXT, started_at TEXT);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, ts TEXT, level TEXT, msg TEXT
);
"""


def now():
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def today():
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")


def connect(path):
    con = sqlite3.connect(path, timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")
    con.executescript(SCHEMA)
    return con


class tx:
    """BEGIN IMMEDIATE ... COMMIT block."""

    def __init__(self, con):
        self.con = con

    def __enter__(self):
        self.con.execute("BEGIN IMMEDIATE")
        return self.con

    def __exit__(self, et, ev, tb):
        self.con.execute("ROLLBACK" if et else "COMMIT")
        return False


def log(con, job_id, msg, level="info"):
    con.execute("INSERT INTO events(job_id, ts, level, msg) VALUES (?,?,?,?)", (job_id, now(), level, msg))


def j(v):
    return json.dumps(v, ensure_ascii=False, sort_keys=True)


def unj(v, default=None):
    return json.loads(v) if v else default


def job_counts(con, job_id):
    q = lambda sql: con.execute(sql, (job_id,)).fetchone()[0]
    retail_offers = q("SELECT COUNT(*) FROM offers WHERE job_id=? AND kind='retail'")
    new = q("SELECT COUNT(*) FROM job_products WHERE job_id=? AND relation='new'")
    uncertain = q("SELECT COUNT(*) FROM job_products WHERE job_id=? AND relation='uncertain'")
    prior = q("SELECT COUNT(*) FROM job_products WHERE job_id=? AND relation='duplicate_prior'")
    return {
        "raw_rows": q("SELECT COUNT(*) FROM raw_rows r JOIN batches b ON b.id=r.batch_id WHERE r.job_id=? AND b.purpose='discovery'"),
        "retail_offers": retail_offers,
        "duplicates": retail_offers - new - uncertain,
        "seen_in_prior_jobs": prior,
        "new_products": new,
        "uncertain_matches": uncertain,
        "evidence_pending": q("SELECT COUNT(*) FROM qualifications WHERE job_id=? AND status='PENDING'"),
        "rejected": q("SELECT COUNT(*) FROM qualifications WHERE job_id=? AND status='REJECTED'"),
        "ready_to_test": q("SELECT COUNT(*) FROM qualifications WHERE job_id=? AND status='QUALIFIED'"),
        "batches_total": q("SELECT COUNT(*) FROM batches WHERE job_id=?"),
        "batches_done": q("SELECT COUNT(*) FROM batches WHERE job_id=? AND status IN ('ingested','failed','aborted')"),
        "batches_in_flight": q("SELECT COUNT(*) FROM batches WHERE job_id=? AND status IN ('submitting','submitted','running','succeeded','uncertain')"),
    }


def spend_today(con):
    rows = con.execute("SELECT kind, COALESCE(SUM(amount),0) s FROM spend WHERE day=? GROUP BY kind", (today(),)).fetchall()
    out = {"actual": 0.0, "estimate": 0.0}
    for r in rows:
        out[r["kind"]] = r["s"]
    return out


def job_spend(con, job_id):
    rows = con.execute("SELECT provider, kind, COALESCE(SUM(amount),0) s FROM spend WHERE job_id=? GROUP BY provider, kind", (job_id,)).fetchall()
    return [dict(r) for r in rows]
