"""Independent demand evidence, kept separate from retail discovery.

Adapter: SerpApi Google Trends API (https://serpapi.com/google-trends-api).
- Credential: SERPAPI_API_KEY (member's own SerpApi account).
- Coverage: any Google Trends geo, including LB, MA, SA, OM.
- Cost: one SerpApi search credit per query (plans priced per month; see serpapi.com/pricing).
A search-results URL is never treated as evidence; only the returned interest time series is.
"""
import json
import socket
import urllib.error
import urllib.parse
import urllib.request

from . import db

PROVIDER = "serpapi_google_trends"


class DemandUnavailable(Exception):
    pass


class SerpApiTrends:
    name = PROVIDER

    def __init__(self, api_key, timeout=60):
        self.api_key = api_key
        self.timeout = timeout

    def fetch(self, query, geo, timeframe):
        if not self.api_key:
            raise DemandUnavailable("SERPAPI_API_KEY not configured")
        params = {"engine": "google_trends", "q": query, "geo": geo, "date": timeframe,
                  "data_type": "TIMESERIES", "api_key": self.api_key}
        url = "https://serpapi.com/search.json?" + urllib.parse.urlencode(params)
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as r:
                data = json.loads(r.read())
        except (urllib.error.URLError, socket.timeout) as e:
            raise DemandUnavailable("SerpApi request failed: %s" % e)
        if data.get("error"):
            raise DemandUnavailable("SerpApi: %s" % data["error"])
        return data


def summarize(data, query, geo):
    """Extract observed values from a Google Trends timeseries response."""
    timeline = (data.get("interest_over_time") or {}).get("timeline_data") or []
    points = []
    for t in timeline:
        for v in t.get("values") or []:
            val = v.get("extracted_value")
            if val is not None:
                points.append({"date": t.get("date"), "value": val})
    if not points:
        return {"query": query, "geo": geo, "points": 0, "avg": None, "last": None,
                "note": "Google Trends returned no data for this query in this geography"}
    vals = [p["value"] for p in points]
    half = len(vals) // 2 or 1
    return {"query": query, "geo": geo, "points": len(vals), "avg": round(sum(vals) / len(vals), 1),
            "first_half_avg": round(sum(vals[:half]) / half, 1),
            "second_half_avg": round(sum(vals[half:]) / max(1, len(vals) - half), 1),
            "last": vals[-1], "start": points[0]["date"], "end": points[-1]["date"],
            "series": points}


def get_demand(con, provider, query, geo, timeframe, job_id, est_cost):
    """Cached fetch. Returns (status, summary, cost_usd, cost_source)."""
    row = con.execute("SELECT response FROM demand_cache WHERE provider=? AND query=? AND geo=? AND timeframe=?",
                      (provider.name, query, geo, timeframe)).fetchone()
    if row:
        return "ok", summarize(json.loads(row["response"]), query, geo), 0.0, "cache"
    try:
        data = provider.fetch(query, geo, timeframe)
    except DemandUnavailable as e:
        return "unavailable", {"query": query, "geo": geo, "error": str(e)}, 0.0, None
    con.execute("INSERT OR REPLACE INTO demand_cache VALUES (?,?,?,?,?,?)",
                (provider.name, query, geo, timeframe, json.dumps(data), db.now()))
    con.execute("INSERT INTO spend(day, job_id, provider, amount, kind, ts) VALUES (?,?,?,?,?,?)",
                (db.today(), job_id, provider.name, est_cost, "estimate", db.now()))
    return "ok", summarize(data, query, geo), est_cost, "estimate"
