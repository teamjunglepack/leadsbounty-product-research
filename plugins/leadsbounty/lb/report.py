"""Leads Bounty HTML report, printable PDF and CSV evidence export."""
import csv
import html
import json
import os
import shutil
import subprocess
import tempfile

from . import brand, db, pdf

T = brand.THEME
E = lambda v: html.escape("" if v is None else str(v))  # noqa: E731


def _link(url, text=None):
    if not url:
        return "—"
    if str(url).startswith("data:"):
        return E(text or "embedded")
    return '<a href="%s" target="_blank" rel="noopener">%s</a>' % (E(url), E(text or _short(url)))


def _short(url):
    u = str(url).split("//", 1)[-1]
    return u[:48] + ("…" if len(u) > 48 else "")


def _img(url, size=72):
    if not url:
        return '<div class="noimg" style="width:%dpx;height:%dpx">IMAGE UNAVAILABLE</div>' % (size, size)
    return '<img class="thumb" src="%s" width="%d" height="%d" alt="" loading="lazy" onerror="this.outerHTML=\'<div class=noimg style=width:%dpx;height:%dpx>IMAGE FAILED</div>\'">' % (
        E(url), size, size, size, size)


CSS = """
:root{--ink:%(ink)s;--muted:%(muted)s;--paper:%(paper)s;--panel:%(panel)s;--line:%(line)s;--accent:%(accent)s;
--pass:%(pass)s;--fail:%(fail)s;--pending:%(pending)s}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:14px/1.45 %(font)s}
.wrap{max-width:1180px;margin:0 auto;padding:24px 16px}
header{display:flex;justify-content:space-between;align-items:center;border-bottom:3px solid var(--accent);padding-bottom:12px}
h1{font-size:22px;margin:16px 0 4px}h2{font-size:17px;margin:28px 0 10px;border-bottom:1px solid var(--line);padding-bottom:6px}
.sub{color:var(--muted)}.fixture{background:#fff3bf;border:2px dashed #e67700;padding:10px 14px;font-weight:700;margin:12px 0}
.partial{background:#fff4e6;border-left:4px solid var(--pending);padding:10px 14px;margin:12px 0}
.complete{background:#ebfbee;border-left:4px solid var(--pass);padding:10px 14px;margin:12px 0}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:8px}
.kpi{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:10px}.kpi b{display:block;font-size:22px}
.kpi span{color:var(--muted);font-size:12px}
table{width:100%%;border-collapse:collapse;font-size:12.5px}th,td{border-bottom:1px solid var(--line);padding:6px;text-align:left;vertical-align:top}
th{background:var(--panel);position:sticky;top:0}thead{display:table-header-group}tr{page-break-inside:avoid}
.thumb{object-fit:cover;border-radius:4px;border:1px solid var(--line);background:var(--panel)}
.noimg{display:flex;align-items:center;justify-content:center;text-align:center;font-size:9px;color:var(--fail);border:1px dashed var(--fail);border-radius:4px}
.badge{display:inline-block;padding:1px 7px;border-radius:10px;font-size:11px;font-weight:700;color:#fff}
.QUALIFIED{background:var(--pass)}.REJECTED{background:var(--fail)}.PENDING{background:var(--pending)}.UNCERTAIN{background:#6741d9}
.card{border:1px solid var(--line);border-radius:8px;padding:14px;margin:12px 0;page-break-inside:avoid}
.card .top{display:flex;gap:14px}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.angle{background:var(--panel);border-radius:6px;padding:8px 10px;margin:6px 0}.hook{font-size:16px;direction:rtl;text-align:right}
footer{margin-top:36px;border-top:1px solid var(--line);padding-top:10px;color:var(--muted);font-size:12px;display:flex;justify-content:space-between}
a{color:#1864ab;word-break:break-all}
@media (max-width:700px){.grid2{grid-template-columns:1fr}.card .top{flex-direction:column}}
@media print{.wrap{max-width:none;padding:0}th{position:static}a{color:var(--ink)}@page{size:A4 landscape;margin:12mm}}
""" % T


def collect(con, cfg, job_id):
    job = dict(con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
    counts = db.job_counts(con, job_id)
    spend = db.job_spend(con, job_id)
    prods = []
    for r in con.execute("""SELECT p.*, jp.relation, jp.uncertain_of, jp.score, q.status, q.reasons, q.economics, q.uncertainty
                            FROM job_products jp JOIN products p ON p.id=jp.product_id
                            LEFT JOIN qualifications q ON q.job_id=jp.job_id AND q.product_id=p.id
                            WHERE jp.job_id=? AND jp.relation IN ('new','uncertain') ORDER BY
                            CASE q.status WHEN 'QUALIFIED' THEN 0 WHEN 'PENDING' THEN 1 WHEN 'REJECTED' THEN 2 ELSE 3 END, p.created_at""", (job_id,)):
        d = dict(r)
        d["reasons"] = db.unj(d["reasons"], [])
        d["economics"] = db.unj(d["economics"], {})
        d["offers"] = [dict(o) for o in con.execute("SELECT * FROM offers WHERE product_id=? AND kind='retail' ORDER BY is_local DESC, price_usd", (d["id"],))]
        d["evidence"] = [dict(e) for e in con.execute("SELECT * FROM evidence WHERE job_id=? AND product_id=?", (job_id, d["id"]))]
        c = con.execute("SELECT angles, generator FROM creatives WHERE job_id=? AND product_id=?", (job_id, d["id"])).fetchone()
        d["angles"] = json.loads(c["angles"]) if c else None
        d["angle_generator"] = c["generator"] if c else None
        prods.append(d)
    build = db.unj(job["actor_build"], {}) or {}
    demand_status = con.execute("SELECT status, COUNT(*) n FROM evidence WHERE job_id=? AND kind='demand' GROUP BY status", (job_id,)).fetchall()
    return job, counts, spend, prods, build, {r["status"]: r["n"] for r in demand_status}


def html_report(con, cfg, job_id):
    job, counts, spend, prods, build, dstat = collect(con, cfg, job_id)
    country = cfg["countries"][job["country"]]
    cp = db.unj(job["checkpoint"], {}) or {}
    actual = sum(s["s"] for s in spend if s["kind"] == "actual")
    est = sum(s["s"] for s in spend if s["kind"] == "estimate")
    out = ['<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
           "<title>%s — %s</title><style>%s</style></head><body><div class=wrap>" % (E(brand.PRODUCT_NAME), E(job_id), CSS)]
    out.append("<header><div>%s</div><div class=sub>%s</div></header>" % (brand.logo_html(), E(brand.PRODUCT_NAME)))
    out.append("<h1>%s · %s · target %d new products</h1><div class=sub>Job %s · created %s · updated %s</div>" % (
        E(brand.PRODUCT_NAME), E(country["name"]), job["target"], E(job_id), E(job["created_at"]), E(job["updated_at"])))
    if job["mode"] == "fixture":
        out.append('<div class=fixture>FIXTURE DEMO — synthetic data, no money spent. Nothing in this report is real market evidence. COD assumptions shown are demo values, not yours.</div>')
    if job["status"] == "complete":
        out.append("<div class=complete><b>COMPLETE</b> — target reached: %d new distinct products.</div>" % counts["new_products"])
    else:
        out.append("<div class=partial><b>%s</b> — %d of %d new products. Stopping reason: %s.<br>Resumable checkpoint: plan item %s; "
                   "resume with <code>/leadsbounty resume %s</code>.</div>" % (
                       E(job["status"].upper()), counts["new_products"], job["target"], E(job["stop_reason"]),
                       E(cp.get("plan_index")), E(job_id)))
    k = [("raw_rows", "Raw rows"), ("retail_offers", "Seller offers"), ("duplicates", "Duplicate offers"),
         ("seen_in_prior_jobs", "Seen in earlier jobs"), ("new_products", "New products"), ("uncertain_matches", "Uncertain matches"),
         ("evidence_pending", "Evidence pending"), ("rejected", "Rejected"), ("ready_to_test", "Ready to test")]
    out.append("<div class=kpis>%s</div>" % "".join("<div class=kpi><b>%s</b><span>%s</span></div>" % (counts[a], b) for a, b in k))
    out.append("<p class=sub>Provider cost: actual USD %.4f (reported by provider) · estimated USD %.4f (not yet reported; estimate only). "
               "Apify actor apify/e-commerce-scraping-tool build %s. Demand provider: %s.</p>" % (
                   actual, est, E(build.get("build_number", "n/a")), E(", ".join("%s=%s" % kv for kv in dstat.items()) or "not run")))

    qualified = [p for p in prods if p["status"] == "QUALIFIED"]
    out.append("<h2>Ready to test (%d)</h2>" % len(qualified))
    if not qualified:
        out.append("<p class=sub>No product passed every gate in this run. See pending reasons below.</p>")
    for p in qualified:
        out.append(product_card(p, country))

    out.append("<h2>All discoveries (%d)</h2><table><thead><tr><th>Image</th><th>Product</th><th>Discovery reason</th>"
               "<th>Offers</th><th>Status</th><th>Why</th></tr></thead><tbody>" % len(prods))
    for p in prods:
        st = "UNCERTAIN" if p["relation"] == "uncertain" else (p["status"] or "PENDING")
        why = ("Possible duplicate of %s (similarity %.2f); not counted as new until reviewed" % (p["uncertain_of"], p["score"] or 0)
               if p["relation"] == "uncertain" else "<br>".join(E(r) for r in p["reasons"]) or "Not evaluated yet")
        out.append("<tr><td>%s</td><td><b>%s</b><br>%s</td><td>%s</td><td>%d</td><td><span class='badge %s'>%s</span></td><td>%s</td></tr>" % (
            _img(p["image"], 56), E(p["title"]), _link(p["source_url"]), E(p["discovery_reason"]), len(p["offers"]), st, st,
            why if p["relation"] == "uncertain" else why))
    out.append("</tbody></table>")
    out.append("<footer><span>%s · %s</span><span>%s</span></footer>" % (
        E(brand.PUBLISHER), '<a href="%s">%s</a>' % (brand.WEBSITE_URL, brand.WEBSITE), E(brand.PRODUCT_NAME)))
    out.append("</div></body></html>")
    return "".join(out), job, counts, prods


def product_card(p, country):
    eco = p["economics"]
    sup = eco.get("supplier", {})
    local = [o for o in p["offers"] if o["is_local"]]
    comp = "".join("<li>%s — %s %s (USD %s) · %s · observed %s</li>" % (
        E(o["seller"]), E(o["price"]), E(o["currency"]), E(o["price_usd"]), _link(o["url"]), E(o["observed_at"])) for o in local[:5])
    ev = {e["kind"]: e for e in p["evidence"]}
    dem = json.loads(ev["demand"]["value"]) if "demand" in ev else {}
    img = json.loads(ev["image_check"]["value"])["detail"] if "image_check" in ev else "not checked"
    econ_rows = "".join("<tr><td>%s</td><td>%s</td></tr>" % (E(a), E(eco.get(b))) for a, b in [
        ("Selling price (USD)", "selling_price"), ("Supplier price (USD)", "supplier_price"), ("Landed cost (USD)", "landed_cost"),
        ("Profit per delivered order before ads", "profit_per_delivered_before_ads"), ("Loss per failed delivery", "loss_per_failed_delivery"),
        ("Confirmation rate", "confirmation_rate"), ("Delivery rate", "delivery_rate"),
        ("Break-even ad cost per submitted lead (USD)", "breakeven_cpl")])
    if p["angles"]:
        angles = "".join("<div class=angle><div class=hook>%s</div><div class=sub>%s</div><b>Demo:</b> %s<br><b>Observation:</b> %s</div>" % (
            E(a["hook"]), E(a.get("hook_translation")), E(a["demo"]), E(a["observation"])) for a in p["angles"])
        angles += "<div class=sub>Generated by %s after qualification.</div>" % E(p["angle_generator"])
    else:
        angles = ("<div class=sub>Creative pending. Run <code>/leadsbounty report %s</code> in Claude Code to write the three angles "
                  "from this evidence, or set ANTHROPIC_API_KEY for unattended generation.</div>" % E(p["first_job_id"]))
    return ("<div class=card><div class=top>%s<div><b style='font-size:16px'>%s</b> <span class='badge QUALIFIED'>QUALIFIED</span>"
            "<div class=sub>Variant: %s · pack %s · image check: %s</div><div>Retail link: %s</div>"
            "<div>Supplier: %s (%s)</div></div></div>"
            "<div class=grid2><div><h3>Local competitor prices (%s)</h3><ul>%s</ul><h3>Demand</h3><p>Google Trends via SerpApi, %s, "
            "%s to %s: average %s, last %s · %s</p><h3>Why it passed</h3><ul>%s</ul><p><b>Biggest uncertainty:</b> %s</p></div>"
            "<div><h3>COD economics</h3><table>%s</table><h3>Marketing angles</h3>%s</div></div></div>") % (
        _img(p["image"], 120), E(p["title"]), E(p["variant"] or "standard"), E(p["pack_count"]), E(img), _link(p["source_url"]),
        _link(sup.get("url")), E(sup.get("detail")), E(country["name"]), comp, E(dem.get("geo")), E(dem.get("start")), E(dem.get("end")),
        E(dem.get("avg")), E(dem.get("last")), _link(ev.get("demand", {}).get("source_url"), "trends link"),
        "".join("<li>%s</li>" % E(r) for r in p["reasons"]), E(p["uncertainty"]), econ_rows, angles)


def write_csv(path, job, prods):
    cols = ["job_id", "mode", "product_id", "relation", "status", "title", "variant", "pack_count", "discovery_reason", "reasons",
            "offer_source", "offer_is_local", "seller", "offer_url", "price", "currency", "price_usd", "observed_at", "image",
            "demand_status", "demand_query", "demand_geo", "demand_period", "demand_avg", "image_check", "supplier_url",
            "supplier_price_usd", "breakeven_cpl", "biggest_uncertainty"]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for p in prods:
            ev = {e["kind"]: e for e in p["evidence"]}
            dem = json.loads(ev["demand"]["value"]) if "demand" in ev else {}
            sup = p["economics"].get("supplier", {})
            base = [job["id"], job["mode"], p["id"], p["relation"], p["status"], p["title"], p["variant"], p["pack_count"],
                    p["discovery_reason"], " | ".join(p["reasons"])]
            tail = [ev.get("demand", {}).get("status", "not collected"), dem.get("query"), dem.get("geo"),
                    "%s..%s" % (dem.get("start"), dem.get("end")) if dem.get("start") else "", dem.get("avg"),
                    json.loads(ev["image_check"]["value"])["detail"] if "image_check" in ev else "not checked",
                    sup.get("url"), sup.get("price_usd"), p["economics"].get("breakeven_cpl"), p["uncertainty"]]
            for o in p["offers"] or [{}]:
                img = o.get("image") or p["image"]
                w.writerow(base + [o.get("source_id"), o.get("is_local"), o.get("seller"), o.get("url"), o.get("price"), o.get("currency"),
                                   o.get("price_usd"), o.get("observed_at"), "embedded fixture image" if str(img).startswith("data:") else img] + tail)


def find_browser():
    cands = [os.environ.get("LEADSBOUNTY_PDF_BROWSER"), shutil.which("msedge"), shutil.which("chrome"), shutil.which("google-chrome"),
             shutil.which("chromium"), shutil.which("chromium-browser"),
             r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
             r"C:\Program Files\Google\Chrome\Application\chrome.exe",
             "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
    for c in cands:
        if c and os.path.exists(c):
            return c
    return None


def write_pdf(html_path, pdf_path, job, counts, prods):
    """Print the HTML report with a locally installed Chrome/Edge (rendering only, no browsing).
    Falls back to a built-in plain PDF writer so a printable PDF always exists."""
    b = find_browser()
    if b:
        profile = tempfile.mkdtemp(prefix="lb-pdf-")
        try:
            subprocess.run([b, "--headless=new", "--disable-gpu", "--no-first-run", "--user-data-dir=" + profile,
                            "--no-pdf-header-footer", "--print-to-pdf=" + pdf_path, "file:///" + html_path.replace("\\", "/")],
                           capture_output=True, timeout=120)
        except (OSError, subprocess.SubprocessError):
            pass
        finally:
            shutil.rmtree(profile, ignore_errors=True)
        if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 1000:
            return "browser"
    lines = pdf.report_lines(job, counts, prods)
    pdf.write_text_pdf(pdf_path, lines)
    return "builtin"


def generate(con, cfg, paths, job_id):
    out_dir = os.path.join(paths.reports, job_id)
    os.makedirs(out_dir, exist_ok=True)
    doc, job, counts, prods = html_report(con, cfg, job_id)
    hp = os.path.join(out_dir, "report.html")
    with open(hp, "w", encoding="utf-8") as f:
        f.write(doc)
    write_csv(os.path.join(out_dir, "evidence.csv"), job, prods)
    write_pdf(os.path.abspath(hp), os.path.join(out_dir, "report.pdf"), job, counts, prods)
    return out_dir
