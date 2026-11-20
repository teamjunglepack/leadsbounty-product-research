"""Branded discovery-catalog report: Top Sellers and Newly Listed first, then labelled reference lists.
Outputs report.html, report.pdf, catalog.csv and catalog.json (complete raw records)."""
import csv
import json
import os

from . import brand, catalog, db, pdf
from .report import CSS, E, _img, _link, write_pdf

EXTRA_CSS = """
.grp{margin-top:28px}.cnt{font-weight:400;color:var(--muted);font-size:14px}
.short{background:#fff4e6;border-left:4px solid var(--pending);padding:8px 12px;margin:8px 0}
.ref{background:#f1f3f5;border-left:4px solid #868e96;padding:8px 12px;margin:8px 0}
.prod{border:1px solid var(--line);border-radius:8px;padding:12px;margin:10px 0;page-break-inside:avoid}
.prod .top{display:flex;gap:12px}.ev{font-size:12.5px;margin-top:4px}.tag{display:inline-block;background:var(--panel);border:1px solid var(--line);
border-radius:10px;padding:0 7px;margin:2px 4px 2px 0;font-size:11.5px}.ok{background:#ebfbee;border-color:#b2f2bb}
details{margin-top:6px}summary{cursor:pointer;color:#1864ab}dl{display:grid;grid-template-columns:200px 1fr;gap:2px 10px;font-size:12.5px;margin:6px 0}
dt{color:var(--muted)}dd{margin:0;word-break:break-word}
@media (max-width:700px){dl{grid-template-columns:1fr}}
"""


def _fmt(v):
    if v in (None, "", [], {}):
        return '<span class=sub>not returned</span>'
    if isinstance(v, dict):
        return "<br>".join("%s: %s" % (E(k), E(x)) for k, x in v.items())
    if isinstance(v, list):
        return "<br>".join(E(x) for x in v[:15])
    return E(v)


def product_block(p, label, cfg):
    pe = p["primary"]
    evs = p["evidence"]
    ev_lines = []
    if p.get("best_rank"):
        r = p["best_rank"]
        ev_lines.append("<b>%s #%d</b> in <i>%s</i> on %s · window: %s · observed %s · %s" % (
            E(r["list"]), r["rank"], E(r["rank_category"]), E(r.get("marketplace") or pe["marketplace"]),
            E(r["window"]), E(r["observed_at"][:10]), _link(r["evidence_url"], "ranking evidence")))
    if p.get("new_release_rank"):
        r = p["new_release_rank"]
        ev_lines.append("New Releases #%d in <i>%s</i> · %s · %s" % (r["rank"], E(r["rank_category"]), E(r["window"]), _link(r["evidence_url"], "evidence")))
    if p.get("listing"):
        d = p["listing"]
        ev_lines.append("<b>Listing date %s</b> (raw: “%s”) · source: %s · %s" % (E(d["listing_date"]), E(d["listing_date_raw"]),
                                                                              E(d["listing_date_source"]), _link(d["url"], "evidence")))
    elif p.get("new_undated"):
        ev_lines.append("New Arrival — date unavailable (the product page returned no first-available date)")
    links = "".join("<div>%s · %s · %s</div>" % (E(e["marketplace"]), E(e["region_relation"]), _link(e["url"])) for e in evs)
    dl = "".join("<dt>%s</dt><dd>%s</dd>" % (k, _fmt(v)) for k, v in [
        ("Price", ("%s %s · %s%s" % (pe.get("price"), pe.get("currency") or "", pe.get("price_label"),
                                     (" · currency " + pe["currency_source"]) if pe.get("currency_source") else "")) if pe.get("price") is not None else None),
        ("Brand", pe.get("brand")), ("Seller / supplier", pe.get("seller")),
        ("Alibaba supplier", (pe.get("supplier") or {}).get("name") if pe.get("supplier") else None),
        ("MOQ / price tiers", (pe["supplier"]["note"]) if pe.get("supplier") else None),
        ("Rating", pe.get("rating")), ("Reviews", pe.get("reviews")), ("Stock", pe.get("stock")),
        ("Shipping", pe.get("shipping")), ("Marketplace category", pe.get("marketplace_category")),
        ("Variants", pe.get("variants")), ("Pack contents", pe.get("pack_contents")), ("Identifiers", pe.get("identifiers")),
        ("Features", pe.get("features")), ("Specifications", pe.get("specifications")),
        ("Description", (pe.get("description") or "")[:1200] or None), ("Images", len(pe.get("images") or []) or None)])
    return ("<div class=prod><div class=top>%s<div><b>%s</b> <span class='tag ok'>%s</span><div class=sub>%s · %s · requested category: %s</div>"
            "<div>%s</div><div class=ev>%s</div><div>%s</div></div></div>"
            "<details><summary>All product details</summary><dl>%s</dl><div class=sub>Other source links:</div>%s</details></div>") % (
        _img(pe.get("image"), 96), E(pe["title"]), E(label), E(", ".join(p["sources"])), E(pe["region_relation"]),
        E(pe["category_requested"]), _link(pe["url"], "Open product page"), "<br>".join(ev_lines) or "—",
        "".join("<span class=tag>%s</span>" % E(b) for b in p["badges"]), dl, links)


def build(con, cfg, job_id):
    job = dict(con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
    cp = db.unj(job["checkpoint"], {})
    params = cp["params"]
    import datetime as _dt
    period = catalog.parse_period(params["period_text"], _dt.date.fromisoformat(params["requested_on"]))
    prods = catalog.classify(con, job_id, period)
    sel = catalog.select(prods, params["total"])
    return job, cp, params, period, prods, sel


def shortfall(group, sel, period, cp):
    q = sel["quota"][group]
    got = len(sel["groups"][group])
    if got >= q:
        return None
    if group == "top":
        why = ("The sources returned no ranking or sales metric covering %s. Amazon Best Sellers is a current ranking (updated hourly); "
               "Alibaba returned no sales ranking; specialist marketplaces returned no data." % period["label"]) if not catalog.top_period_ok(period) else \
            "Not enough products with a current Best Sellers rank were found before the run stopped."
    else:
        undated = len(sel["new_undated"])
        why = "Only %d product(s) had a marketplace listing date inside %s. %d New Releases product(s) had no listing date." % (got, period["label"], undated)
    stop = cp.get("stop_reason_detail")
    return "Shortfall: %d of %d (%d missing). %s%s" % (got, q, q - got, why, (" Stop: " + stop) if stop else "")


def html(con, cfg, job_id):
    job, cp, params, period, prods, sel = build(con, cfg, job_id)
    q = sel["quota"]
    cname = "Worldwide" if params["target"] == "WW" else cfg["countries"][params["target"]]["name"]
    out = ['<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
           "<title>%s — Discovery %s</title><style>%s%s</style></head><body><div class=wrap>" % (E(brand.PRODUCT_NAME), E(job_id), CSS, EXTRA_CSS),
           "<header><div>%s</div><div class=sub>%s</div></header>" % (brand.logo_html(), E(brand.PRODUCT_NAME)),
           "<h1>Marketplace discovery · %s · %s · %s</h1>" % (E(cname), E(params["category_text"]), E(period["label"])),
           "<div class=sub>Job %s · period %s to %s · requested %d distinct products = %d Top Sellers + %d Newly Listed · updated %s</div>" % (
               E(job_id), period["start"], period["end"], params["total"], q["top"], q["new"], E(job["updated_at"]))]
    if job["mode"] == "fixture":
        out.append("<div class=fixture>FIXTURE DEMO — synthetic data, no money spent. Nothing in this report is real marketplace data.</div>")
    out.append("<div class=%s><b>%s</b> — verified: %d/%d Top Sellers · %d/%d Newly Listed. Stop: %s.%s</div>" % (
        "complete" if job["status"] == "complete" else "partial", E(job["status"].upper()), len(sel["groups"]["top"]), q["top"],
        len(sel["groups"]["new"]), q["new"], E(job["stop_reason"]),
        "" if job["status"] == "complete" else " Resume with <code>/leadsbounty resume %s</code>." % E(job_id)))
    out.append("<p class=sub>This is a discovery catalog: no demand checks, COD calculations or sales recommendations. You decide what to sell.</p>")

    for group, title, label in (("top", "Top Sellers", "Top Seller — verified for period"), ("new", "Newly Listed", "Newly Listed — verified date")):
        items = sel["groups"][group]
        out.append("<h2 class=grp>%s <span class=cnt>%d verified of %d requested</span></h2>" % (title, len(items), q[group]))
        sf = shortfall(group, sel, period, cp)
        if sf:
            out.append("<div class=short>%s</div>" % E(sf))
        out.extend(product_block(p, label, cfg) for p in items)
        if group == "top" and sel["current_bestsellers"]:
            cur = sel["current_bestsellers"][: q["top"]]
            out.append("<div class=ref><b>Current bestseller — observed today</b> (%d shown of %d found). Not counted in the verified %s total: "
                       "the source reports a current ranking, not a %s ranking.</div>" % (len(cur), len(sel["current_bestsellers"]), title, E(period["label"])))
            out.extend(product_block(p, "Current bestseller — observed today", cfg) for p in cur)
        if group == "new" and sel["new_undated"]:
            und = sel["new_undated"][: q["new"]]
            out.append("<div class=ref><b>New Arrival — date unavailable</b> (%d shown of %d found). On a New Releases list, but the product page "
                       "returned no listing date, so it is not counted as Newly Listed.</div>" % (len(und), len(sel["new_undated"])))
            out.extend(product_block(p, "New Arrival — date unavailable", cfg) for p in und)

    others = [p for p in sel["extras"] if not p.get("best_rank") and not p.get("listing")]
    if others:
        by_src = {}
        for p in others:
            by_src.setdefault(p["sources"][0], []).append(p)
        out.append("<h2>Other discoveries <span class=cnt>not counted in either group</span></h2>")
        out.append("<div class=ref>These products came from category pages that return no ranking, sales metric or listing date "
                   "(for example Alibaba category search). Up to %d per source shown; all are in catalog.csv and catalog.json.</div>" % max(q["top"], q["new"]))
        for src, items in sorted(by_src.items()):
            out.append("<h3>%s <span class=cnt>%d found</span></h3>" % (E(src), len(items)))
            out.extend(product_block(p, "Discovery — no ranking or date returned", cfg) for p in items[: max(q["top"], q["new"])])
    cov = cp.get("coverage", {})
    out.append("<h2>Source coverage</h2><table><thead><tr><th>Source</th><th>Marketplace / list</th><th>Region relation</th><th>Runs</th>"
               "<th>Rows returned</th><th>Result</th></tr></thead><tbody>")
    for key, c in sorted(cov.items()):
        dead = cp.get("dead_sources", {}).get(key)
        out.append("<tr><td>%s</td><td>%s · %s</td><td>%s</td><td>%d</td><td>%d</td><td>%s</td></tr>" % (
            E(c["source"]), E(c["marketplace"]), E(c["list_type"]), E(c["relation"]), c["runs"], c["rows"],
            E("returned data" if c["rows"] else ("no data — " + dead if dead else "no data yet"))))
    out.append("</tbody></table>")
    out.append("<p class=sub>Saved extra candidates not shown in the requested results: %d (all in catalog.csv and catalog.json). "
               "Uncertain matches (possible duplicates, not counted): %d. Products already found in earlier jobs and not recounted: %d. "
               "Raw rows scraped: %d.</p>" % (
                   len(sel["extras"]), sum(1 for p in sel["seen_before"] if p["relation"] == "uncertain"),
                   sum(1 for p in sel["seen_before"] if p["relation"] != "uncertain"),
                   con.execute("SELECT COUNT(*) FROM raw_rows WHERE job_id=?", (job_id,)).fetchone()[0]))
    out.append("<footer><span>%s · <a href='%s'>%s</a></span><span>%s</span></footer></div></body></html>" % (
        E(brand.PUBLISHER), brand.WEBSITE_URL, brand.WEBSITE, E(brand.PRODUCT_NAME)))
    return "".join(out), job, sel, prods, params, period


def rows_for_export(sel, prods, params):
    placed = {}
    for g, lab in (("top", "Top Sellers (verified)"), ("new", "Newly Listed (verified)")):
        for p in sel["groups"][g]:
            placed[p["product_id"]] = lab
    for p in sel["current_bestsellers"]:
        placed.setdefault(p["product_id"], "Current bestseller — observed today")
    for p in sel["new_undated"]:
        placed.setdefault(p["product_id"], "New Arrival — date unavailable")
    for p in prods.values():
        placed.setdefault(p["product_id"], {"new": "Extra candidate", "uncertain": "Uncertain match (possible duplicate, not counted)"}.get(
            p["relation"], "Seen in an earlier job (not recounted)"))
    return placed


def write_csv(path, sel, prods, params, job):
    placed = rows_for_export(sel, prods, params)
    cols = ["job_id", "mode", "group", "product_id", "title", "product_url", "marketplace", "storefront_region", "requested_country",
            "region_relation", "requested_category", "marketplace_category", "list_type", "rank_list", "rank", "rank_category",
            "rank_window", "rank_evidence_url", "listing_date", "listing_date_raw", "listing_date_source", "price", "currency",
            "currency_source", "price_label", "brand", "seller_or_supplier", "moq_and_tiers", "rating", "reviews", "stock", "shipping",
            "identifiers", "image", "images", "variants", "pack_contents", "features", "specifications", "description", "badges", "observed_at",
            "sales_volume", "sales_window", "product_page_bestseller_ranks", "discovery_query", "discovery_page", "list_price", "possible_duplicate"]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for pid, p in prods.items():
            for e in p["evidence"]:
                r = e.get("rank") or {}
                w.writerow([job["id"], job["mode"], placed[pid], pid, e["title"], e["url"], e["marketplace"], e["storefront_region"],
                            params["target"], e["region_relation"], e["category_requested"], e["marketplace_category"], e["list_type"],
                            r.get("list"), r.get("rank"), r.get("rank_category"), r.get("window"), r.get("evidence_url"), e["listing_date"],
                            e["listing_date_raw"], e["listing_date_source"], e["price"], e["currency"], e["currency_source"], e["price_label"],
                            e["brand"], e["seller"], (e.get("supplier") or {}).get("note"), e["rating"], e["reviews"], e["stock"], e["shipping"],
                            json.dumps(e["identifiers"], ensure_ascii=False), "embedded fixture image" if str(e["image"]).startswith("data:") else e["image"],
                            len(e["images"] or []), json.dumps(e["variants"], ensure_ascii=False), json.dumps(e["pack_contents"], ensure_ascii=False),
                            " | ".join(e["features"] or []), json.dumps(e["specifications"], ensure_ascii=False), (e["description"] or "")[:3000],
                            " | ".join(p["badges"]), e["observed_at"],
                            (e.get("sales") or {}).get("text"), (e.get("sales") or {}).get("window_text"),
                            " | ".join("#%d in %s" % (r["rank"], r["rank_category"]) for r in e.get("page_ranks") or []),
                            (e.get("found_by") or {}).get("query"), (e.get("found_by") or {}).get("page"),
                            json.dumps(e.get("list_price"), ensure_ascii=False) if e.get("list_price") else None,
                            p.get("possible_duplicate")])


def write_json(path, con, sel, prods, params, job, cp):
    placed = rows_for_export(sel, prods, params)
    raws = {}
    for r in con.execute("""SELECT ce.product_id, rr.payload FROM catalog_evidence ce JOIN raw_rows rr ON rr.batch_id=ce.batch_id
                            AND rr.row_index=ce.row_index WHERE ce.job_id=?""", (job["id"],)):
        raws.setdefault(r["product_id"], []).append(json.loads(r["payload"]))
    doc = {"product": brand.PRODUCT_NAME, "publisher": brand.PUBLISHER, "job": {k: job[k] for k in ("id", "mode", "status", "stop_reason", "created_at", "updated_at")},
           "fixture": job["mode"] == "fixture", "request": params, "quota": sel["quota"],
           "verified_counts": {g: len(sel["groups"][g]) for g in ("top", "new")}, "coverage": cp.get("coverage"), "dead_sources": cp.get("dead_sources"),
           "products": [{"product_id": pid, "group": placed[pid], "badges": p["badges"], "sources": p["sources"], "evidence": p["evidence"],
                         "raw_records": raws.get(pid, [])} for pid, p in prods.items()]}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1, default=str)


def generate(con, cfg, paths, job_id, pdf_export=False):
    """report.html = interactive discovery dashboard (main product); CSV + JSON downloads; PDF only on request."""
    from . import dashboard
    out_dir = os.path.join(paths.reports, job_id)
    os.makedirs(out_dir, exist_ok=True)
    catalog.reextract(con, job_id)  # apply the current extractor to the saved raw rows (no API calls)
    job, cp, params, period, prods, sel = build(con, cfg, job_id)
    hp = os.path.join(out_dir, "report.html")
    with open(hp, "w", encoding="utf-8") as f:
        f.write(dashboard.render(dashboard.build_data(con, cfg, job_id)))
    write_csv(os.path.join(out_dir, "catalog.csv"), sel, prods, params, job)
    write_json(os.path.join(out_dir, "catalog.json"), con, sel, prods, params, job, cp)
    if not pdf_export:
        return out_dir
    doc = html(con, cfg, job_id)[0]  # static print layout
    pp = os.path.join(out_dir, "print.html")
    with open(pp, "w", encoding="utf-8") as f:
        f.write(doc)
    hp = pp
    lines = [brand.PRODUCT_NAME + " - marketplace discovery", "Job %s  %s  %s  %s" % (job_id, params["target"], params["category_text"], period["label"])]
    if job["mode"] == "fixture":
        lines.append("FIXTURE DEMO - synthetic data")
    for g, t in (("top", "TOP SELLERS"), ("new", "NEWLY LISTED")):
        lines.append("%s: %d verified of %d" % (t, len(sel["groups"][g]), sel["quota"][g]))
        for p in sel["groups"][g]:
            lines.append("  %s | %s" % (p["primary"]["title"], p["primary"]["url"]))
    lines.append("Current bestsellers observed today: %d; New arrivals without date: %d" % (len(sel["current_bestsellers"]), len(sel["new_undated"])))
    lines += ["", brand.PUBLISHER + " - " + brand.WEBSITE]
    b = write_pdf(os.path.abspath(hp), os.path.join(out_dir, "report.pdf"), job, {}, [])
    if b == "builtin":
        pdf.write_text_pdf(os.path.join(out_dir, "report.pdf"), lines)
    return out_dir
