"""Member config, paths and credentials. Secrets are never stored in the project."""
import copy
import json
import os

PKG_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PKG_DIR, "data")
PLUGIN_ROOT = os.path.dirname(PKG_DIR)

CREDENTIAL_KEYS = ("APIFY_TOKEN", "SERPAPI_API_KEY", "ANTHROPIC_API_KEY")


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def find_project_dir(start=None):
    """Nearest ancestor holding .leadsbounty/ or .claude/, else the start dir."""
    start = os.path.abspath(start or os.environ.get("LEADSBOUNTY_PROJECT") or os.getcwd())
    home = os.path.abspath(os.path.expanduser("~"))
    d = start
    while True:
        # ~/.leadsbounty holds only the private credentials file; it never marks a project
        if os.path.abspath(d) != home and os.path.exists(os.path.join(d, ".leadsbounty", "config.json")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    d = start
    while True:
        if os.path.isdir(os.path.join(d, ".claude")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return start
        d = parent


class Paths:
    def __init__(self, project_dir):
        self.project = project_dir
        self.home = os.path.join(project_dir, ".leadsbounty")
        self.config = os.path.join(self.home, "config.json")
        self.db = os.path.join(self.home, "research.sqlite")
        self.reports = os.path.join(self.home, "reports")
        self.logs = os.path.join(self.home, "logs")
        self.worker_lock = os.path.join(self.home, "worker.lock")

    def ensure(self):
        for d in (self.home, self.reports, self.logs):
            os.makedirs(d, exist_ok=True)


def _deep_merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def default_config():
    cfg = load_json(os.path.join(DATA_DIR, "default_config.json"))
    countries = load_json(os.path.join(DATA_DIR, "countries.json"))
    cfg["countries"] = {k: v for k, v in countries.items() if not k.startswith("_")}
    cfg["categories"] = countries["_categories"]
    cfg["supplier_source"] = countries["_supplier_source"]
    return cfg


def load_config(paths):
    base = default_config()
    if os.path.exists(paths.config):
        return _deep_merge(base, load_json(paths.config))
    return base


def set_config_value(paths, dotted, raw_value):
    """Set a member config value, e.g. budget.daily_spend_cap_usd 5."""
    data = load_json(paths.config) if os.path.exists(paths.config) else {}
    try:
        value = json.loads(raw_value)
    except ValueError:
        value = raw_value
    cur = data
    keys = dotted.split(".")
    for k in keys[:-1]:
        cur = cur.setdefault(k, {})
    cur[keys[-1]] = value
    save_json(paths.config, data)
    return value


def resolve_country(cfg, text):
    t = (text or "").strip().lower()
    for code, c in cfg["countries"].items():
        if t == code.lower() or t in [a.lower() for a in c.get("aliases", [])]:
            return code
    raise ValueError("Unknown country '%s'. Supported: %s" % (
        text, ", ".join("%s (%s)" % (c["name"], k) for k, c in cfg["countries"].items())))


def credentials_file():
    return os.environ.get("LEADSBOUNTY_CREDENTIALS") or os.path.join(
        os.path.expanduser("~"), ".leadsbounty", "credentials.env")


def load_credentials():
    """Environment variables win; otherwise read the member's user-level credentials file."""
    creds = {}
    path = credentials_file()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                v = v.strip().strip('"').strip("'")
                if k.strip() in CREDENTIAL_KEYS and v:
                    creds[k.strip()] = v
    for k in CREDENTIAL_KEYS:
        if os.environ.get(k):
            creds[k] = os.environ[k]
    return creds


def credential_status():
    c = load_credentials()
    return {k: ("configured" if c.get(k) else "missing") for k in CREDENTIAL_KEYS}
