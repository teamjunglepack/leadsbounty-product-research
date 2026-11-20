"""Apify REST API client (server-side, token auth). No browser automation anywhere."""
import hashlib
import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.apify.com/v2"


class ApifyError(Exception):
    pass


class UncertainSubmission(ApifyError):
    """The start-run request may or may not have created a run (timeout / connection reset)."""


class SchemaError(ApifyError):
    pass


def input_hash(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def validate_input(payload, schema):
    """Validate a payload against the actor's published input schema (subset of JSON Schema it uses)."""
    props = schema.get("properties", {})
    errors = []
    for req in schema.get("required", []) or []:
        if req not in payload:
            errors.append("missing required field %s" % req)
    for k, v in payload.items():
        p = props.get(k)
        if p is None:
            errors.append("unknown field %s (not in actor input schema)" % k)
            continue
        t = p.get("type")
        ok = {"string": isinstance(v, str), "integer": isinstance(v, int) and not isinstance(v, bool),
              "boolean": isinstance(v, bool), "array": isinstance(v, list), "object": isinstance(v, dict),
              "number": isinstance(v, (int, float)) and not isinstance(v, bool)}.get(t, True)
        if not ok:
            errors.append("%s must be %s" % (k, t))
            continue
        if "enum" in p and v not in p["enum"]:
            errors.append("%s=%r not in allowed values" % (k, v))
        if t == "array" and isinstance(p.get("items"), dict) and "enum" in p["items"]:
            bad = [x for x in v if x not in p["items"]["enum"]]
            if bad:
                errors.append("%s contains unsupported values %s" % (k, bad))
        if t in ("integer", "number"):
            if p.get("minimum") is not None and v < p["minimum"]:
                errors.append("%s below minimum %s" % (k, p["minimum"]))
            if p.get("maximum") is not None and v > p["maximum"]:
                errors.append("%s above maximum %s" % (k, p["maximum"]))
    if errors:
        raise SchemaError("; ".join(errors))
    return True


class ApifyClient:
    def __init__(self, token, actor_id="apify~e-commerce-scraping-tool", timeout=60, sleep=time.sleep):
        if not token:
            raise ApifyError("APIFY_TOKEN is not configured")
        self.token = token
        self.actor_id = actor_id
        self.timeout = timeout
        self.sleep = sleep

    # -- transport -------------------------------------------------------
    def _req(self, method, path, params=None, body=None, retries=5, idempotent=True):
        url = API + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        delay = 2.0
        for attempt in range(retries + 1):
            req = urllib.request.Request(url, data=data, method=method)
            req.add_header("Authorization", "Bearer " + self.token)
            req.add_header("User-Agent", "leadsbounty-product-research")
            if data is not None:
                req.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    raw = r.read()
                    return json.loads(raw) if raw else None
            except urllib.error.HTTPError as e:
                if e.code == 429 or e.code >= 500:
                    if not idempotent and e.code >= 500:
                        raise UncertainSubmission("HTTP %s on non-idempotent request" % e.code)
                    if attempt == retries:
                        raise ApifyError("HTTP %s after retries" % e.code)
                    ra = e.headers.get("Retry-After") if e.headers else None
                    self.sleep(float(ra) if ra and ra.replace(".", "").isdigit() else delay)
                    delay = min(delay * 2, 60)
                    continue
                msg = e.read().decode("utf-8", "replace")[:500]
                raise ApifyError("HTTP %s: %s" % (e.code, msg))
            except (urllib.error.URLError, socket.timeout, ConnectionError) as e:
                if not idempotent:
                    raise UncertainSubmission(str(e))
                if attempt == retries:
                    raise ApifyError("network error: %s" % e)
                self.sleep(delay)
                delay = min(delay * 2, 60)

    # -- actor / build ---------------------------------------------------
    def latest_build(self):
        actor = self._req("GET", "/acts/%s" % self.actor_id)["data"]
        tb = (actor.get("taggedBuilds") or {}).get("latest") or {}
        build_id = tb.get("buildId")
        build = self._req("GET", "/actor-builds/%s" % build_id)["data"]
        schema = build.get("inputSchema") or (build.get("actorDefinition") or {}).get("input")
        if isinstance(schema, str):
            schema = json.loads(schema)
        return {"build_id": build_id, "build_number": build.get("buildNumber") or tb.get("buildNumber"),
                "schema": schema, "pricing": actor.get("pricingInfos")}

    # -- runs ------------------------------------------------------------
    def start_run(self, payload, build_number=None, max_charge_usd=None):
        params = {}
        if build_number:
            params["build"] = build_number
        if max_charge_usd:
            params["maxTotalChargeUsd"] = max_charge_usd
        # Never auto-retried: a lost response could still have created a paid run.
        return self._req("POST", "/acts/%s/runs" % self.actor_id, params, payload, retries=0, idempotent=False)["data"]

    def get_run(self, run_id):
        return self._req("GET", "/actor-runs/%s" % run_id)["data"]

    def recent_runs(self, limit=50):
        return self._req("GET", "/acts/%s/runs" % self.actor_id, {"desc": 1, "limit": limit})["data"]["items"]

    def run_input(self, run):
        kv = run.get("defaultKeyValueStoreId")
        if not kv:
            return None
        try:
            return self._req("GET", "/key-value-stores/%s/records/INPUT" % kv)
        except ApifyError:
            return None

    def find_run_for_input(self, payload, since_iso):
        """Reconcile an uncertain submission: look for a run started after since_iso with identical input."""
        want = input_hash(payload)
        for run in self.recent_runs():
            if (run.get("startedAt") or "") < since_iso[:19]:
                continue
            full = self.get_run(run["id"])
            inp = self.run_input(full)
            if inp is not None and input_hash(inp) == want:
                return full
        return None

    def dataset_items(self, dataset_id, offset, limit=200):
        return self._req("GET", "/datasets/%s/items" % dataset_id,
                         {"offset": offset, "limit": limit, "clean": 1, "format": "json"})

    def abort_run(self, run_id):
        return self._req("POST", "/actor-runs/%s/abort" % run_id, idempotent=True)


def run_cost(run):
    """Return (amount, 'actual') when Apify reports cost, else (None, None)."""
    for key in ("usageTotalUsd",):
        if run.get(key) is not None:
            amount = float(run[key])
            # Pay-per-event charges are reported separately from platform usage.
            ppe = run.get("chargedEventCounts") and run.get("pricingInfo", {}).get("pricingPerEvent")
            if ppe:
                events = ppe.get("actorChargeEvents", {})
                for ev, n in run["chargedEventCounts"].items():
                    price = events.get(ev, {}).get("eventPriceUsd")
                    if price is not None:
                        amount += price * n
            return round(amount, 6), "actual"
    return None, None
