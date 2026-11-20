"""Leads Bounty product discovery dashboard (report.html): a single self-contained page.

Tabs: Products (one table, view buttons All Discovered / Newly Listed / Top Selling / Unmatched, filters, sorting,
expandable rows) and Scrape Report (history of every research job and every provider run, with spend).
"""
import datetime as _dt
import json
import re

from . import brand, catalog, db, history

STOREFRONT_NAMES = {
    "www.amazon.ae": "Amazon UAE", "www.amazon.sa": "Amazon Saudi Arabia", "www.amazon.com": "Amazon US",
    "www.amazon.co.uk": "Amazon UK", "www.amazon.fr": "Amazon France", "www.amazon.de": "Amazon Germany",
    "www.amazon.eg": "Amazon Egypt", "www.alibaba.com": "Alibaba", "www.autozone.com": "AutoZone (US)",
    "www.cvs.com": "CVS (US)", "www.boots.com": "Boots (UK)", "www.bestbuy.com": "Best Buy (US)", "www.ikea.com": "IKEA",
    "www.chewy.com": "Chewy (US)", "www.dickssportinggoods.com": "Dick's Sporting Goods (US)",
}
AMZ_SIZE = re.compile(r"\._[A-Z0-9_,]+_(\.(?:jpg|jpeg|png|webp))$", re.I)


def storefront(mk):
    return STOREFRONT_NAMES.get(mk, (mk or "").replace("www.", ""))


def marketplace_name(mk):
    mk = mk or ""
    for k, v in (("amazon.", "Amazon"), ("alibaba.", "Alibaba"), ("boots.", "Boots"), ("cvs.", "CVS"), ("autozone.", "AutoZone")):
        if k in mk:
            return v
    return storefront(mk)


def full_image(u):
    if not u or not isinstance(u, str):
        return None
    return AMZ_SIZE.sub(r"\1", u) if "media-amazon.com/images/I/" in u else u


def gallery(evs):
    out = []
    for e in evs:
        for u in [e.get("image")] + list(e.get("images") or []):
            if not u or "/images/G/" in str(u):
                continue
            f = full_image(u)
            if f and f not in out:
                out.append(f)
    return out[:12]


def short_relation(rel):
    r = (rel or "").split("(")[0].strip()
    return r[:1].upper() + r[1:] if r else ""


def short_title(t, n=64):
    t = (t or "").strip()
    if len(t) <= n:
        return t
    return t[:n].rsplit(" ", 1)[0].rstrip(",;:-–|") + "…"


def nice_date(iso, with_year=True):
    if not iso:
        return None
    d = _dt.date.fromisoformat(iso[:10])
    return d.strftime("%b ") + str(d.day) + (d.strftime(", %Y") if with_year else "")


def observed(iso, today):
    return "observed today" if iso and iso[:10] == today else "observed %s" % nice_date(iso)


def product_record(p, period, today):
    evs = p["evidence"]
    pe = p["primary"]
    ev = []
    for r in p["ranks"][:3]:
        ev.append({"kind": "top", "text": "#%d in %s · %s" % (r["rank"], r["rank_category"], observed(r["observed_at"], today)),
                   "match": p["rank_match"], "why": "current ranking" + ("" if p["rank_match"] else " · not tied to the selected period"),
                   "src": "%s %s" % (storefront(r["marketplace"]), r["source"]), "url": r["evidence_url"]})
    if p["sales"]:
        s = p["sales"]
        ev.append({"kind": "top", "text": "%s · %s" % (s["text"], observed(s["observed_at"], today)), "match": p["sales_match"],
                   "why": "sales window %s" % (s["window_text"] or "not stated") + (" fits the selected period" if p["sales_match"] else " does not fit the selected period"),
                   "src": storefront(s["marketplace"]), "url": s["evidence_url"]})
    if p["listing"]:
        d = p["listing"]
        ev.append({"kind": "new" if p["new_verified"] else "date", "text": "First available %s" % nice_date(d["listing_date"]),
                   "match": p["new_verified"], "why": "listing date %s the selected period" % ("inside" if p["new_verified"] else "outside"),
                   "src": d["listing_date_source"], "url": d["url"]})
    if p["new_release_rank"] and not p["listing"]:
        r = p["new_release_rank"]
        ev.append({"kind": "new", "text": "New Releases badge · date unknown", "match": False,
                   "why": "New Releases #%d in %s; product page gave no first-available date" % (r["rank"], r["rank_category"]),
                   "src": storefront(r["marketplace"]), "url": r["evidence_url"]})
    deep = [r for r in p["all_page_ranks"] if r["rank"] > catalog.TOP_RANK_LIMIT]
    found_by = []
    for e in evs:
        fb = e.get("found_by") or {}
        found_by.append({"storefront": storefront(e["marketplace"]), "list": (fb.get("list_type") or e["list_type"]).replace("_", " "),
                         "query": fb.get("query"), "page": fb.get("page"), "url": e["url"], "observed": nice_date(e["observed_at"])})
    mcat = pe.get("marketplace_category") or ""
    cat_top = mcat.split(" > ")[0] if mcat else None
    sup = pe.get("supplier") or {}
    rank_best = p["best_rank"]["rank"] if p["best_rank"] else None
    return {
        "id": p["product_id"], "title": pe["title"], "short": short_title(pe["title"]), "url": pe["url"],
        "image": full_image(pe.get("image")) or (gallery(evs) or [None])[0], "images": gallery(evs),
        "mk": marketplace_name(pe["marketplace"]), "storefront": storefront(pe["marketplace"]),
        "storefronts": sorted({storefront(e["marketplace"]) for e in evs}),
        "relation": pe["region_relation"], "relation_short": short_relation(pe["region_relation"]),
        "category": cat_top, "mcat": mcat, "req_category": pe.get("category_requested"),
        "price": pe.get("price"), "currency": pe.get("currency"), "price_label": pe.get("price_label"),
        "currency_source": pe.get("currency_source"), "list_price": pe.get("list_price"), "price_range": pe.get("price_range"),
        "wholesale": pe["source"] == "alibaba",
        "sales": p["sales"]["text"] if p["sales"] else None, "sales_units": (p["sales"] or {}).get("min_units"),
        "rating": pe.get("rating"), "reviews": pe.get("reviews"), "rank": rank_best,
        "listing_date": p["listing"]["listing_date"] if p["listing"] else None,
        "moq": sup.get("moq"), "tiers": sup.get("price_tiers"),
        "top": p["top_any"], "top_match": p["top_verified"], "new": p["new_any"], "new_match": p["new_verified"],
        "unmatched": p["unmatched"], "dup": p["possible_duplicate"], "seen_before": p["relation"] == "duplicate_prior",
        "ev": ev, "deep_ranks": ["#%d in %s" % (r["rank"], r["rank_category"]) for r in deep[:3]],
        "found_by": found_by, "badges": p["badges"],
        "details": {"Brand": pe.get("brand"), "Seller / supplier": pe.get("seller") or sup.get("name"),
                    "MOQ": sup.get("moq") if pe.get("supplier") else None,
                    "Price tiers": sup.get("price_tiers") if pe.get("supplier") else None,
                    "List price": ("%s %s" % (pe["list_price"]["currency"], pe["list_price"]["value"])) if pe.get("list_price") else None,
                    "Stock": pe.get("stock"), "Shipping / delivery": pe.get("shipping"), "Marketplace category": mcat or None,
                    "Variants": pe.get("variants"), "Variant options": pe.get("variant_options"), "Pack contents": pe.get("pack_contents"),
                    "Identifiers": pe.get("identifiers"), "Features": pe.get("features"), "Specifications": pe.get("specifications"),
                    "Description": (pe.get("description") or "")[:2500] or None},
        "is_alibaba": pe["source"] == "alibaba",
    }


def build_data(con, cfg, job_id):
    job = dict(con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
    cp = db.unj(job["checkpoint"], {})
    params = cp["params"]
    period = catalog.parse_period(params["period_text"], _dt.date.fromisoformat(params["requested_on"]))
    prods = catalog.classify(con, job_id, period)
    sel = catalog.select(prods, params["total"])
    today = db.today()
    records = [product_record(p, period, today) for p in prods.values()]
    by_id = {r["id"]: r for r in records}
    for p in prods.values():
        if p["uncertain_of"] and p["uncertain_of"] in by_id:
            by_id[p["product_id"]]["dup_of"] = by_id[p["uncertain_of"]]["short"]
    q = sel["quota"]
    code = params["target"]
    cname = "Worldwide" if code == "WW" else cfg["countries"].get(code, {}).get("name", code)
    fronts = catalog.storefronts_for(cfg, code)
    return {
        "brand": {"product": brand.PRODUCT_NAME, "publisher": brand.PUBLISHER, "site": brand.WEBSITE, "url": brand.WEBSITE_URL},
        "job": {"id": job_id, "mode": job["mode"], "status": job["status"], "stop": job["stop_reason"], "created": job["created_at"],
                "updated": job["updated_at"], "build": (db.unj(job["actor_build"], {}) or {}).get("build_number")},
        "request": {"market": cname, "period": period["label"], "snapshot": bool(period.get("snapshot")),
                    "period_range": "%s – %s" % (nice_date(period["start"].isoformat()), nice_date(period["end"].isoformat())),
                    "new_rule": period.get("new_rule"), "category": params["category_text"], "total": params["total"], "quota": q,
                    "verified": {"top": len(sel["groups"]["top"]), "new": len(sel["groups"]["new"])},
                    "storefronts": [{"name": storefront(mk), "relation": rel} for mk, region, rel in fronts]},
        "products": records,
        "history": history.build(con, cfg),
        "current_job": job_id,
        "generated": db.now(),
        "top_rank_limit": catalog.TOP_RANK_LIMIT,
    }


def render(data):
    payload = json.dumps(data, ensure_ascii=False, default=str).replace("</", "<\\/")
    title = "%s · %s · %s" % (data["request"]["market"], data["request"]["category"], data["request"]["period"])
    logo = brand.logo_html() if brand.logo_data_uri() else \
        '<span class="wordmark">LEADS<span style="color:var(--accent)"> BOUNTY</span></span>'
    return TEMPLATE.replace("__TITLE__", "Product Discovery — " + title.replace("<", "")).replace("__DATA__", payload) \
        .replace("__ACCENT__", brand.THEME["accent"]).replace("__ACCENT_DARK__", brand.THEME["accent_dark_mode"]).replace("__LOGO__", logo)


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#14171c;--muted:#5d6472;--line:#e3e6eb;--soft:#f1f3f6;--accent:__ACCENT__;
--ok:#1f7a45;--okbg:#e6f5ec;--warn:#8a5300;--warnbg:#fff3df;--info:#1c4f8f;--infobg:#e7eff9;--grey:#5d6472;--greybg:#eef0f3;--vio:#5b3cc4;--viobg:#efeafc}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#0f1115;--card:#171a20;--ink:#e8eaee;--muted:#9aa2b1;--line:#2a2f38;--soft:#1e222a;
--accent:__ACCENT_DARK__;--okbg:#12301f;--warnbg:#33260d;--infobg:#132539;--greybg:#22262e;--viobg:#241c3d;--ok:#5fd394;--warn:#f0b35a;--info:#8fb6ee;--vio:#b39dfa}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 -apple-system,"Segoe UI",Roboto,"Noto Sans Arabic",Arial,sans-serif}
a{color:var(--info)}.wrap{max-width:1320px;margin:0 auto;padding:16px}
header.top{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;border-bottom:3px solid var(--accent);padding-bottom:10px}
.brandline{display:flex;align-items:center;gap:12px}.wordmark{font-weight:800;letter-spacing:.12em;color:var(--ink)}.sub{color:var(--muted)}
.btn{border:1px solid var(--line);background:var(--card);color:var(--ink);padding:6px 11px;border-radius:8px;cursor:pointer;font:inherit;text-decoration:none;display:inline-flex;align-items:center;gap:6px}
.btn:hover{border-color:var(--accent)}.actions{display:flex;gap:8px;flex-wrap:wrap}
.req{display:flex;flex-wrap:wrap;gap:6px 18px;align-items:baseline;margin:14px 0 4px}
.req .k{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin-right:4px}.req .v{font-weight:700;font-size:16px}
.summary{display:flex;flex-wrap:wrap;gap:6px 14px;color:var(--muted);font-size:13px;margin:4px 0 10px}
.fixture{background:var(--warnbg);color:var(--warn);border:2px dashed currentColor;border-radius:8px;padding:8px 12px;font-weight:700;margin:10px 0}
.maintabs{display:flex;gap:4px;border-bottom:1px solid var(--line);margin-top:8px}
.mtab{padding:10px 16px;border:0;background:none;color:var(--muted);font:inherit;font-weight:700;cursor:pointer;border-bottom:3px solid transparent}
.mtab.on{color:var(--ink);border-bottom-color:var(--accent)}
.views{display:flex;gap:6px;flex-wrap:wrap;margin:12px 0 8px}
.view{border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:999px;padding:6px 14px;font:inherit;cursor:pointer}
.view b{font-weight:700}.view .n{color:var(--muted);margin-left:4px}.view.on{background:var(--accent);border-color:var(--accent);color:#fff}.view.on .n{color:#fff;opacity:.85}
.filters{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px;display:grid;gap:8px}
.frow{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.frow input[type=search]{flex:1 1 240px}
input,select{padding:6px 8px;border:1px solid var(--line);border-radius:7px;background:var(--card);color:var(--ink);font:inherit}
input[type=number]{width:96px}input[type=date]{width:140px}
.flab{display:inline-flex;gap:5px;align-items:center;font-size:13px;color:var(--muted)}
.cov{font-size:12px;color:var(--muted)}.cov b{color:var(--ink);font-weight:600}
.count{margin:10px 0 6px;font-weight:600}.count .sub{font-weight:400}
.tablewrap{background:var(--card);border:1px solid var(--line);border-radius:10px;overflow-x:auto}
table{width:100%;border-collapse:collapse;min-width:900px}
th{background:var(--soft);text-align:left;font-size:12px;color:var(--muted);font-weight:600;padding:8px;border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:8px;border-bottom:1px solid var(--line);vertical-align:middle}
tr.row:hover td{background:var(--soft)}
.prod{display:flex;gap:10px;align-items:center;min-width:280px}
.thumb{width:52px;height:52px;border-radius:6px;object-fit:contain;background:#fff;border:1px solid var(--line);flex:none}
.noimg{width:52px;height:52px;border-radius:6px;border:1px dashed var(--line);display:flex;align-items:center;justify-content:center;font-size:10px;color:var(--muted);flex:none}
.pname{font-weight:600;line-height:1.3;text-decoration:none;color:var(--ink)}.pname:hover{text-decoration:underline}
.small{font-size:12px;color:var(--muted)}.mk{font-weight:600}.price{white-space:nowrap;font-variant-numeric:tabular-nums;font-weight:600}
.pill{display:inline-block;padding:1px 8px;border-radius:999px;font-size:11.5px;font-weight:600;white-space:nowrap;margin:1px 0}
.p-ok{background:var(--okbg);color:var(--ok)}.p-warn{background:var(--warnbg);color:var(--warn)}.p-info{background:var(--infobg);color:var(--info)}
.p-grey{background:var(--greybg);color:var(--grey)}.p-vio{background:var(--viobg);color:var(--vio)}
.evline{font-size:12.5px;line-height:1.35;margin:2px 0}.evline .m{font-size:11px}
.exp{border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:6px;cursor:pointer;width:30px;height:30px;font:inherit}
.detail td{background:var(--soft);padding:14px}
.gal{display:flex;gap:6px;flex-wrap:wrap;margin:6px 0 10px}.gal img{width:88px;height:88px;object-fit:contain;background:#fff;border:1px solid var(--line);border-radius:6px}
.dgrid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:16px}
dl{display:grid;grid-template-columns:150px minmax(0,1fr);gap:4px 10px;margin:0;font-size:13px}dt{color:var(--muted)}dd{margin:0;word-break:break-word}
.nr{color:var(--muted);font-style:italic}ul.feat{margin:2px 0;padding-left:18px}
.pager{display:flex;gap:8px;align-items:center;justify-content:flex-end;margin:10px 0;color:var(--muted)}
.empty{padding:28px;text-align:center;color:var(--muted)}
.kpis{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0}.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px}
.kpi .v{font-size:20px;font-weight:700}.kpi .k{font-size:12px;color:var(--muted)}
.jobrow{cursor:pointer}.runs td{background:var(--soft)}.runs table{min-width:0}
footer{margin:28px 0 8px;padding-top:10px;border-top:1px solid var(--line);color:var(--muted);font-size:12px;display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px}
@media (max-width:760px){.dgrid{grid-template-columns:1fr}dl{grid-template-columns:1fr}}
@media print{.actions,.filters,.views,.maintabs,.pager,.exp{display:none!important}body{background:#fff}table{min-width:0}}
</style></head><body><div class="wrap">
<header class="top"><div class="brandline">__LOGO__<span class="sub">Product discovery dashboard</span></div>
<div class="actions"><a class="btn" href="catalog.csv" download>CSV</a><a class="btn" href="catalog.json" download>JSON</a>
<button class="btn" onclick="window.print()">Print / PDF</button></div></header>
<div id="fixture"></div>
<div class="req" id="req"></div>
<div class="summary" id="summary"></div>
<nav class="maintabs"><button class="mtab on" data-t="products">Products</button><button class="mtab" data-t="report">Scrape Report</button></nav>
<section id="t-products"></section>
<section id="t-report" hidden></section>
<footer><span id="pub"></span><span id="gen"></span></footer>
</div>
<script id="lb-data" type="application/json">__DATA__</script>
<script>
(function(){
const D=JSON.parse(document.getElementById('lb-data').textContent), R=D.request, J=D.job, ALL=D.products;
const $=(s,el=document)=>el.querySelector(s), esc=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const safe=u=>(/^(https?:|data:image\/)/i.test(u||''))?u:'';
const img=(u,cls='thumb')=>safe(u)?`<img class="${cls}" src="${esc(safe(u))}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.outerHTML='<div class=noimg>no image</div>'">`:'<div class="noimg">no image</div>';
const fmt=(n,d=2)=>Number(n).toLocaleString(undefined,{minimumFractionDigits:d,maximumFractionDigits:d});
const usd=n=>n==null?'—':'USD '+fmt(n,4);

if(J.mode==='fixture')$('#fixture').innerHTML='<div class="fixture">FIXTURE DEMO — synthetic data, no money spent.</div>';
$('#req').innerHTML=[['Market',R.market],['Period',R.period+(R.snapshot?'':' · '+R.period_range)],['Category',R.category],['Target',R.total+' products']]
 .map(([k,v])=>`<span><span class="k">${k}</span><span class="v">${esc(v)}</span></span>`).join('');
const st=J.status==='complete'?'<span class="pill p-ok">Complete</span>':J.status==='partial'?'<span class="pill p-warn">Partial</span>':`<span class="pill p-info">${esc(J.status)}</span>`;
$('#summary').innerHTML=`${st}<span>Target ${R.total} = ${R.quota.top} Top Selling + ${R.quota.new} Newly Listed</span>
 <span>Matching ${esc(R.period)}: <b>${R.verified.top}/${R.quota.top}</b> Top Selling · <b>${R.verified.new}/${R.quota.new}</b> Newly Listed</span>
 <span>Storefronts: ${R.storefronts.map(s=>esc(s.name)+' ('+esc(s.relation.split('(')[0].trim())+')').join(', ')}</span>`;
$('#pub').innerHTML=`${esc(D.brand.product)} · ${esc(D.brand.publisher)} · <a href="${esc(D.brand.url)}">${esc(D.brand.site)}</a>`;
$('#gen').textContent='Job '+J.id+' · generated '+D.generated.slice(0,16).replace('T',' ')+' UTC';

// ---------------- Products ----------------
const S={view:'all',q:'',mk:'',sf:'',cat:'',cur:'',pmin:'',pmax:'',match:false,dups:false,sales:false,smin:'',dfrom:'',dto:'',rmin:'',revmin:'',rankmax:'',moqmax:'',sort:'default',page:1,per:25,open:new Set()};
const uniq=f=>[...new Set(ALL.map(f).filter(v=>v!=null&&v!==''))].sort();
const has=f=>ALL.filter(p=>!p.dup).filter(f).length, BASE=ALL.filter(p=>!p.dup).length;
const COV={sales:has(p=>p.sales),date:has(p=>p.listing_date),rating:has(p=>p.rating!=null),reviews:has(p=>p.reviews!=null),rank:has(p=>p.rank!=null),moq:has(p=>p.moq!=null)};
const opt=(v,l)=>`<option value="${esc(v)}">${esc(l==null?v:l)}</option>`;
function metric(key,label,html){return COV[key]?`<span class="flab">${html}</span>`:'';}
$('#t-products').innerHTML=`
 <div class="views" id="views"></div>
 <div class="filters" id="filters">
  <div class="frow"><input type="search" data-f="q" placeholder="Product name or keyword…" aria-label="Search">
   <label class="flab">Marketplace <select data-f="mk"><option value="">All</option>${uniq(p=>p.mk).map(v=>opt(v)).join('')}</select></label>
   <label class="flab">Storefront <select data-f="sf"><option value="">All</option>${uniq(p=>p.storefront).map(v=>opt(v)).join('')}</select></label>
   <label class="flab">Category <select data-f="cat"><option value="">All</option>${uniq(p=>p.category).map(v=>opt(v)).join('')}</select></label></div>
  <div class="frow"><label class="flab">Currency <select data-f="cur"><option value="">All</option>${uniq(p=>p.currency).map(v=>opt(v)).join('')}</select></label>
   <label class="flab">Min price <input type="number" min="0" step="any" data-f="pmin" placeholder="any"></label>
   <label class="flab">Max price <input type="number" min="0" step="any" data-f="pmax" placeholder="any"></label>
   <span class="small" id="pnote">Price filters apply within one currency (never mixed or converted).</span>
   <label class="flab">Sort <select data-f="sort">
    <option value="default">Evidence strength</option><option value="price_asc">Price: low → high</option><option value="price_desc">Price: high → low</option>
    ${COV.sales?'<option value="sales">Sales volume: high → low</option>':''}${COV.rank?'<option value="rank">Rank: best first</option>':''}
    ${COV.date?'<option value="date">Listing date: newest</option>':''}${COV.rating?'<option value="rating">Rating: high → low</option>':''}
    ${COV.reviews?'<option value="reviews">Reviews: most first</option>':''}<option value="name">Name A–Z</option></select></label></div>
  <div class="frow">
   ${metric('sales','',`<input type="checkbox" data-f="sales"> Has sales volume · min units <input type="number" min="0" data-f="smin">`)}
   ${metric('date','',`First available from <input type="date" data-f="dfrom"> to <input type="date" data-f="dto">`)}
   ${metric('rating','',`Rating ≥ <input type="number" min="0" max="5" step="0.1" data-f="rmin">`)}
   ${metric('reviews','',`Reviews ≥ <input type="number" min="0" data-f="revmin">`)}
   ${metric('rank','',`Rank ≤ <input type="number" min="1" data-f="rankmax">`)}
   ${metric('moq','',`MOQ ≤ <input type="number" min="1" data-f="moqmax">`)}
   <label class="flab"><input type="checkbox" data-f="match"> Matches selected period</label>
   <label class="flab"><input type="checkbox" data-f="dups"> Include possible duplicates (${ALL.filter(p=>p.dup).length})</label></div>
  <div class="cov">Optional fields returned by the scraper (of ${BASE} products):
   <b>Sales volume</b> ${COV.sales} · <b>Listing date</b> ${COV.date} · <b>Rating</b> ${COV.rating} · <b>Reviews</b> ${COV.reviews} · <b>Rank ≤${D.top_rank_limit}</b> ${COV.rank} · <b>MOQ</b> ${COV.moq}${COV.moq?'':' (Alibaba returned no MOQ or price tiers)'}.
   Missing values are blank and sort last.</div>
 </div>
 <div class="count" id="count"></div>
 <div class="tablewrap"><table><thead><tr><th>Product</th><th>Marketplace &amp; region</th><th>Price</th><th>Sales volume</th><th>Classification evidence</th><th></th></tr></thead>
 <tbody id="tbody"></tbody></table></div><div class="pager" id="pager"></div>`;

const inView=(p,v)=>v==='all'||(v==='new'&&p.new)||(v==='top'&&p.top)||(v==='unmatched'&&p.unmatched);
function passFilters(p){
  if(p.dup&&!S.dups)return false;
  if(S.q){const q=S.q.toLowerCase(); if(!(p.title+' '+(p.mcat||'')+' '+(p.details['Brand']||'')+' '+(p.details['Seller / supplier']||'')+' '+p.found_by.map(f=>f.query||'').join(' ')).toLowerCase().includes(q))return false;}
  if(S.mk&&p.mk!==S.mk)return false; if(S.sf&&!p.storefronts.includes(S.sf))return false; if(S.cat&&p.category!==S.cat)return false;
  if(S.cur){ if(p.currency!==S.cur)return false;
    if(S.pmin!==''&&(p.price==null||p.price<+S.pmin))return false; if(S.pmax!==''&&(p.price==null||p.price>+S.pmax))return false; }
  if(S.sales&&!p.sales)return false; if(S.smin!==''&&(p.sales_units==null||p.sales_units<+S.smin))return false;
  if(S.dfrom&&(!p.listing_date||p.listing_date<S.dfrom))return false; if(S.dto&&(!p.listing_date||p.listing_date>S.dto))return false;
  if(S.rmin!==''&&(p.rating==null||p.rating<+S.rmin))return false; if(S.revmin!==''&&(p.reviews==null||p.reviews<+S.revmin))return false;
  if(S.rankmax!==''&&(p.rank==null||p.rank>+S.rankmax))return false; if(S.moqmax!==''&&(p.moq==null||p.moq>+S.moqmax))return false;
  if(S.match){ const v=S.view; if(v==='top'&&!p.top_match)return false; if(v==='new'&&!p.new_match)return false; if((v==='all'||v==='unmatched')&&!(p.top_match||p.new_match))return false; }
  return true;
}
function sorter(s){
  const last=(a,b,get,dir)=>{const x=get(a),y=get(b); if(x==null&&y==null)return 0; if(x==null)return 1; if(y==null)return -1; return (x<y?-1:x>y?1:0)*dir;};
  const cur=(a,b)=>(a.currency||'~')<(b.currency||'~')?-1:(a.currency||'~')>(b.currency||'~')?1:0;
  return {price_asc:(a,b)=>(a.price==null)-(b.price==null)||cur(a,b)||last(a,b,p=>p.price,1),
   price_desc:(a,b)=>(a.price==null)-(b.price==null)||cur(a,b)||last(a,b,p=>p.price,-1),
   sales:(a,b)=>last(a,b,p=>p.sales_units,-1), rank:(a,b)=>last(a,b,p=>p.rank,1), date:(a,b)=>last(a,b,p=>p.listing_date,-1),
   rating:(a,b)=>last(a,b,p=>p.rating,-1)||last(a,b,p=>p.reviews,-1), reviews:(a,b)=>last(a,b,p=>p.reviews,-1),
   name:(a,b)=>a.title.localeCompare(b.title),
   default:(a,b)=>((b.top_match||b.new_match)-(a.top_match||a.new_match))||((b.top||b.new)-(a.top||a.new))||last(a,b,p=>p.rank,1)||last(a,b,p=>p.sales_units,-1)}[s];
}
function evHtml(p){
  if(!p.ev.length)return '<span class="small">No rank, sales or listing-date evidence returned</span>'+(p.dup?'<div><span class="pill p-vio">possible duplicate</span></div>':'');
  return p.ev.map(e=>{const cls=e.kind==='top'?'p-info':e.kind==='new'?'p-ok':'p-grey', tag=e.kind==='top'?'Top Selling':e.kind==='new'?'Newly Listed':'Listing date';
    return `<div class="evline"><span class="pill ${cls}">${tag}</span> ${esc(e.text)} <span class="m ${e.match?'':'small'}">${e.match?'✓ matches period':'· outside selected period'}</span></div>`;}).join('')
    +(p.dup?`<div><span class="pill p-vio">possible duplicate</span> <span class="small">${esc(p.dup_of?'of '+p.dup_of:'')}</span></div>`:'');
}
function priceHtml(p){
  if(p.price==null)return '';
  let s=`<div class="price">${esc(p.currency||'')} ${fmt(p.price)}</div><div class="small">${p.wholesale?'wholesale · per unit':'retail'}</div>`;
  if(p.price_range&&p.price_range.min&&p.price_range.max)s+=`<div class="small">range ${esc(p.price_range.min.currency)} ${fmt(p.price_range.min.value)}–${fmt(p.price_range.max.value)}</div>`;
  if(p.list_price)s+=`<div class="small">list ${esc(p.list_price.currency)} ${fmt(p.list_price.value)}</div>`;
  return s;
}
function rowHtml(p){
  return `<tr class="row" data-id="${esc(p.id)}"><td><div class="prod">${img(p.image)}<div><a class="pname" href="${esc(safe(p.url))}" target="_blank" rel="noopener" title="${esc(p.title)}">${esc(p.short)}</a>
    <div class="small">${esc(p.category||p.req_category||'')}${p.rating!=null?` · ★ ${p.rating}`:''}${p.reviews!=null?` (${p.reviews.toLocaleString()} reviews)`:''}</div></div></div></td>
   <td><div class="mk">${esc(p.storefront)}</div><div class="small" title="${esc(p.relation)}">${esc(p.relation_short)}</div>${p.storefronts.length>1?`<div class="small">also ${esc(p.storefronts.filter(s=>s!==p.storefront).join(', '))}</div>`:''}</td>
   <td>${priceHtml(p)}</td><td>${p.sales?`<div>${esc(p.sales)}</div>`:''}</td><td>${evHtml(p)}</td>
   <td><button class="exp" aria-label="Show details" aria-expanded="${S.open.has(p.id)}">${S.open.has(p.id)?'▾':'▸'}</button></td></tr>`+(S.open.has(p.id)?detailHtml(p):'');
}
function val(v){
  if(v==null||v===''||(Array.isArray(v)&&!v.length)||(typeof v==='object'&&!Array.isArray(v)&&!Object.keys(v).length))return '<span class="nr">not returned by marketplace</span>';
  if(Array.isArray(v))return '<ul class="feat">'+v.slice(0,20).map(x=>`<li>${esc(x)}</li>`).join('')+'</ul>';
  if(typeof v==='object')return Object.entries(v).map(([k,x])=>`<div><span class="small">${esc(k)}:</span> ${esc(typeof x==='object'?JSON.stringify(x):x)}</div>`).join('');
  return esc(v);
}
function detailHtml(p){
  const d=p.details, gal=p.images.map(u=>img(u,'')).join('');
  const L=['Brand','Seller / supplier','MOQ','Price tiers','List price','Stock','Shipping / delivery','Marketplace category','Variants','Variant options','Pack contents','Identifiers'];
  const found=p.found_by.map(f=>`<div>${esc(f.storefront)} · ${esc(f.list)}${f.query?` · query “${esc(f.query)}”`:''}${f.page?` · <a href="${esc(safe(f.page))}" target="_blank" rel="noopener">category page</a>`:''} · <a href="${esc(safe(f.url))}" target="_blank" rel="noopener">product link</a> <span class="small">observed ${esc(f.observed)}</span></div>`).join('');
  const evd=p.ev.map(e=>`<div>${esc(e.text)} — ${esc(e.why)} <span class="small">(${esc(e.src||'')})</span> ${e.url?`<a href="${esc(safe(e.url))}" target="_blank" rel="noopener">evidence ↗</a>`:''}</div>`).join('')||'<span class="nr">none returned</span>';
  return `<tr class="detail"><td colspan="6"><div style="font-weight:700">${esc(p.title)}</div><div class="gal">${gal}</div>
   <div class="dgrid"><div><dl><dt>Price</dt><dd>${p.price==null?'<span class="nr">not returned</span>':esc(`${p.currency||''} ${p.price} · ${p.price_label||''}${p.currency_source?' · currency '+p.currency_source:''}`)}</dd>
    ${L.map(k=>`<dt>${k}</dt><dd>${val(d[k])}</dd>`).join('')}</dl></div>
   <div><dl><dt>Why it's here</dt><dd>${found}</dd><dt>Evidence</dt><dd>${evd}</dd>
    ${p.deep_ranks.length?`<dt>Other ranks</dt><dd>${esc(p.deep_ranks.join(' · '))} <span class="small">(below the top ${D.top_rank_limit}; not counted as Top Selling)</span></dd>`:''}
    <dt>Features</dt><dd>${val(d['Features'])}</dd><dt>Specifications</dt><dd>${val(d['Specifications'])}</dd>
    <dt>Description</dt><dd>${d['Description']?esc(d['Description']):'<span class="nr">not returned by marketplace</span>'}</dd></dl></div></div></td></tr>`;
}
function renderProducts(){
  const base=ALL.filter(passFilters);
  const counts={all:base.length,new:base.filter(p=>p.new).length,top:base.filter(p=>p.top).length,unmatched:base.filter(p=>p.unmatched).length};
  $('#views').innerHTML=[['all','All Discovered'],['new','Newly Listed'],['top','Top Selling'],['unmatched','Unmatched']]
    .map(([v,l])=>`<button class="view${S.view===v?' on':''}" data-v="${v}"><b>${l}</b><span class="n">${counts[v]}</span></button>`).join('');
  const list=base.filter(p=>inView(p,S.view)).sort(sorter(S.sort));
  const total=ALL.filter(p=>(S.dups||!p.dup)&&inView(p,S.view)).length;
  const pages=Math.max(1,Math.ceil(list.length/S.per)); S.page=Math.min(S.page,pages);
  $('#count').innerHTML=`${list.length} of ${total} products <span class="sub">${S.view==='all'?'discovered':'in this view'}${S.match?' · matching '+esc(R.period):''}</span>`;
  const sl=list.slice((S.page-1)*S.per,S.page*S.per);
  $('#tbody').innerHTML=sl.length?sl.map(rowHtml).join(''):'<tr><td colspan="6" class="empty">No products match these filters.</td></tr>';
  $('#pager').innerHTML=`<button class="btn" data-pg="-1" ${S.page<=1?'disabled':''}>‹ Prev</button><span>Page ${S.page} / ${pages}</span><button class="btn" data-pg="1" ${S.page>=pages?'disabled':''}>Next ›</button>`;
}
const F=$('#filters');
F.addEventListener('input',e=>{const f=e.target.dataset.f; if(!f)return; S[f]=e.target.type==='checkbox'?e.target.checked:e.target.value; S.page=1;
  if((f==='pmin'||f==='pmax')&&e.target.value!==''&&!S.cur){
    // no currency chosen yet: use the most common currency so prices are never compared across currencies
    const cnt={}; ALL.forEach(p=>{if(p.currency&&p.price!=null)cnt[p.currency]=(cnt[p.currency]||0)+1;});
    const top=Object.keys(cnt).sort((a,b)=>cnt[b]-cnt[a])[0];
    if(top){S.cur=top; F.querySelector('[data-f=cur]').value=top;}
  }
  if(f==='cur'&&!S.cur){F.querySelectorAll('[data-f=pmin],[data-f=pmax]').forEach(i=>{i.value='';S[i.dataset.f]='';});}
  $('#pnote').textContent=S.cur?('Showing '+S.cur+' prices only'+((S.pmin!==''||S.pmax!=='')?' between '+(S.pmin===''?'any':S.pmin)+' and '+(S.pmax===''?'any':S.pmax):'')+'. Change the currency above to compare others.')
    :'Price filters apply within one currency (never mixed or converted).';
  renderProducts();});
$('#views').addEventListener('click',e=>{const b=e.target.closest('.view'); if(!b)return; S.view=b.dataset.v; S.page=1; renderProducts();});
$('#pager').addEventListener('click',e=>{const b=e.target.closest('[data-pg]'); if(!b)return; S.page+=+b.dataset.pg; renderProducts(); window.scrollTo({top:$('#views').offsetTop-10});});
$('#tbody').addEventListener('click',e=>{if(e.target.closest('a'))return; const tr=e.target.closest('tr.row'); if(!tr||!e.target.closest('.exp,td'))return;
  const id=tr.dataset.id; S.open.has(id)?S.open.delete(id):S.open.add(id); renderProducts();});
renderProducts();

// ---------------- Scrape Report ----------------
const H=D.history, T=H.totals;
function covHtml(c){const e=Object.entries(c); return e.length?e.map(([k,v])=>`${esc(k)} <b>${v}</b>`).join(' · '):'—';}
function costHtml(j){ if(j.mode==='fixture')return '<span class="small">fixture · $0</span>';
  return `<b>${usd(j.confirmed)}</b>${j.estimated?`<div class="small">+ ${usd(j.estimated)} estimated</div>`:''}${j.unknown?`<div class="small">${j.unknown} run(s) charge unknown</div>`:''}`;}
$('#t-report').innerHTML=`<div class="kpis">
  <div class="kpi"><div class="v">${usd(T.confirmed)}</div><div class="k">Confirmed spend, all jobs (actual Apify charges, ${T.unique_paid_runs} unique provider runs)</div></div>
  <div class="kpi"><div class="v">${usd(T.estimated)}</div><div class="k">Estimated, not yet confirmed</div></div>
  <div class="kpi"><div class="v">${T.unknown_runs}</div><div class="k">Runs with unknown charge</div></div>
  <div class="kpi"><div class="v">${H.jobs.length}</div><div class="k">Research jobs</div></div></div>
  <div class="small" style="margin-bottom:10px">Each provider run ID is counted once, even after retries, reconciles or resumes. Spend ledger (actual) = ${usd(T.ledger_actual)}${Math.abs(T.ledger_actual-T.confirmed)<1e-6?' ✓ reconciles with the unique runs':' ✗ differs from the unique-run total'}. ${esc(T.note)}</div>
  <div class="tablewrap"><table><thead><tr><th></th><th>Date</th><th>Market</th><th>Category · period</th><th>Target</th><th>Discovered</th><th>Marketplace coverage (raw rows)</th><th>Status</th><th>Cost</th></tr></thead>
  <tbody>${H.jobs.map(j=>`<tr class="jobrow${j.id===D.current_job?' cur':''}" data-j="${esc(j.id)}"><td><button class="exp">▸</button></td>
   <td>${esc(j.date.slice(0,16).replace('T',' '))}<div class="small">${esc(j.id)}${j.id===D.current_job?' · this report':''}</div></td><td>${esc(j.market)}<div class="small">${esc(j.kind)}${j.mode==='fixture'?' · fixture':''}</div></td>
   <td>${esc(j.category)}<div class="small">${esc(j.period)}</div></td><td>${j.target}</td><td>${j.discovered}</td><td class="small">${covHtml(j.coverage)}</td>
   <td>${esc(j.status)}${j.resumed?`<div class="small">resumed ${j.resumed}×</div>`:''}<div class="small">${esc(j.stop||'')}</div></td><td>${costHtml(j)}</td></tr>`).join('')}</tbody></table></div>`;
$('#t-report').addEventListener('click',e=>{const tr=e.target.closest('tr.jobrow'); if(!tr)return; const nx=tr.nextElementSibling;
  if(nx&&nx.classList.contains('runs')){nx.remove(); tr.querySelector('.exp').textContent='▸'; return;}
  const j=H.jobs.find(x=>x.id===tr.dataset.j); tr.querySelector('.exp').textContent='▾';
  tr.insertAdjacentHTML('afterend',`<tr class="runs"><td colspan="9"><table><thead><tr><th>Provider run ID</th><th>Query or category page</th><th>Status</th><th>Raw rows</th><th>New products</th><th>Duplicate rate</th><th>Cost / new product</th><th>Charge</th><th>Submitted</th></tr></thead><tbody>
   ${j.runs.map(r=>`<tr><td>${esc(r.run_id||'—')}${r.duplicate_run_id?' <span class="small">(same run as above; not recounted)</span>':''}</td><td class="small">${esc(r.what)}</td><td>${esc(r.status)}</td><td>${r.raw_rows}</td><td>${r.new_products}</td><td>${r.dup_rate==null?'':Math.round(r.dup_rate*100)+'%'}</td>
    <td>${r.cost_per_new==null?'':usd(r.cost_per_new)}</td><td>${r.charge==null?'<span class="small">unknown</span>':esc(r.charge_kind==='confirmed'?usd(r.charge):r.charge_kind==='fixture'?'$0 (fixture)':r.charge_kind==='not submitted'?'—':usd(r.charge)+' '+r.charge_kind)}</td><td class="small">${esc((r.submitted||'').slice(0,16).replace('T',' '))}</td></tr>`).join('')||'<tr><td colspan=9 class="empty">No provider runs</td></tr>'}</tbody></table></td></tr>`);});

document.querySelector('.maintabs').addEventListener('click',e=>{const b=e.target.closest('.mtab'); if(!b)return;
  document.querySelectorAll('.mtab').forEach(x=>x.classList.toggle('on',x===b));
  $('#t-products').hidden=b.dataset.t!=='products'; $('#t-report').hidden=b.dataset.t!=='report';});
})();
</script></body></html>
"""
