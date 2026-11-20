"""Marketplace product discovery catalog (the /leadsbounty scrap command).

Discovers products from category pages and rankings (Amazon Best Sellers / New Releases, Alibaba category
search, optional specialist marketplaces), scrapes product pages for details, and splits the requested total
into Top Sellers and Newly Listed. No demand checks, COD maths or sales recommendations: the member decides.

Evidence rules
- Top Sellers count as VERIFIED only when the source reports a ranking or sales metric for a window that fits
  the requested period. Amazon Best Sellers is a current ranking (updated hourly), so it is shown as
  "Current bestseller - observed today" and kept outside the verified total for multi-day periods.
- Newly Listed count as VERIFIED only with a marketplace listing date (Amazon "Date First Available") inside
  the period. A "New Releases" appearance without a date is "New Arrival - date unavailable". The date we
  discovered a product, or its latest review, never counts as a listing date.
"""
import datetime as _dt
import html
import json
import re

from . import apify, db, dedupe
from .worker import JobRunner, utcnow

# ---------------------------------------------------------------------------
# configuration (editable through member config "catalog")
# ---------------------------------------------------------------------------
CATEGORIES = {
    # amazon: Best Sellers / New Releases node slug. alibaba: category-level search phrase (not a product keyword).
    "automotive": {"amazon": "automotive", "alibaba": "car accessories",
                   "specialist": [{"id": "autozone", "marketplace": "www.autozone.com", "query": "car accessories",
                                   "label": "AutoZone (automotive specialist, US)"}]},
    "health": {"amazon": "hpc", "alibaba": "health care supplies",
               "specialist": [{"id": "cvs", "marketplace": "www.cvs.com", "query": "health", "label": "CVS (health specialist, US)"},
                              {"id": "boots", "marketplace": "www.boots.com", "query": "health", "label": "Boots (health & beauty, UK)"}]},
    "beauty": {"amazon": "beauty", "alibaba": "beauty personal care",
               "specialist": [{"id": "boots", "marketplace": "www.boots.com", "query": "beauty", "label": "Boots (health & beauty, UK)"}]},
    "kitchen": {"amazon": "kitchen", "alibaba": "kitchen tools", "specialist": []},
    "home": {"amazon": "home", "alibaba": "home decor", "specialist": [{"id": "ikea", "marketplace": "www.ikea.com", "query": "home", "label": "IKEA"}]},
    "electronics": {"amazon": "electronics", "alibaba": "consumer electronics",
                    "specialist": [{"id": "bestbuy", "marketplace": "www.bestbuy.com", "query": "electronics", "label": "Best Buy (electronics, US)"}]},
    "baby": {"amazon": "baby", "alibaba": "baby products", "specialist": []},
    "toys": {"amazon": "toys", "alibaba": "toys", "specialist": []},
    "sports": {"amazon": "sporting", "alibaba": "sports equipment",
               "specialist": [{"id": "dicks", "marketplace": "www.dickssportinggoods.com", "query": "fitness", "label": "Dick's Sporting Goods (US)"}]},
    "pet": {"amazon": "pet-supplies", "alibaba": "pet products", "specialist": [{"id": "chewy", "marketplace": "www.chewy.com", "query": "pet supplies", "label": "Chewy (pet specialist, US)"}]},
}
# Best Sellers node names differ by storefront. Verified live: amazon.sa "health" (2026-11-20; "hpc" returns nothing there),
# amazon.ae "automotive". Others follow Amazon's usual naming and are unverified until a search uses them.
AMAZON_SLUG_BY_STOREFRONT = {
    "health": {"www.amazon.com": "hpc", "www.amazon.co.uk": "drugstore", "default": "health"},
}


def amazon_slug(category, marketplace):
    over = AMAZON_SLUG_BY_STOREFRONT.get(category)
    if over:
        return over.get(marketplace, over["default"])
    return CATEGORIES[category]["amazon"]


ALL_CATEGORIES = ["automotive", "health", "kitchen", "beauty", "electronics", "home"]

# requested country -> Amazon storefronts and how they relate to the country
STOREFRONTS = {
    "LB": [("www.amazon.ae", "AE", "regional reference (UAE storefront; not Lebanon-specific)")],
    "OM": [("www.amazon.ae", "AE", "regional reference (UAE storefront; not Oman-specific)")],
    "MA": [("www.amazon.fr", "FR", "regional reference (France storefront; not Morocco-specific)")],
    "SA": [("www.amazon.sa", "SA", "local storefront (Saudi Arabia)")],
    "AE": [("www.amazon.ae", "AE", "local storefront (UAE)")],
    "EG": [("www.amazon.eg", "EG", "local storefront (Egypt)")],
    "WW": [("www.amazon.com", "US", "worldwide: US storefront"), ("www.amazon.co.uk", "GB", "worldwide: UK storefront"),
           ("www.amazon.ae", "AE", "worldwide: UAE storefront"), ("www.amazon.sa", "SA", "worldwide: Saudi storefront")],
}
STOREFRONT_CURRENCY = {"www.amazon.ae": "AED", "www.amazon.sa": "SAR", "www.amazon.com": "USD", "www.amazon.co.uk": "GBP",
                       "www.amazon.fr": "EUR", "www.amazon.de": "EUR", "www.amazon.eg": "EGP"}
ENGLISH_LANG = {"www.amazon.ae": "en_AE", "www.amazon.sa": "en_AE", "www.amazon.eg": "en_AE"}

SNAPSHOT_NEW_DAYS = 30
BESTSELLER_WINDOW = "current ranking (Amazon Best Sellers, updated hourly)"
NEW_RELEASE_WINDOW = "Amazon Hot New Releases (no fixed date window published)"

# ---------------------------------------------------------------------------
# request parsing
# ---------------------------------------------------------------------------
def parse_period(text, today=None):
    """Return {"start": date, "end": date, "label": str, "days": int}."""
    today = today or utcnow().date()
    t = (text or "").strip().lower()
    if not t:
        raise ValueError("period missing")
    if t in ("current", "current snapshot", "snapshot", "now", "current ranking"):
        # Current snapshot: today's rankings count as Top Sellers; Newly Listed = first available within SNAPSHOT_NEW_DAYS
        return {"start": today - _dt.timedelta(days=SNAPSHOT_NEW_DAYS - 1), "end": today, "days": SNAPSHOT_NEW_DAYS,
                "snapshot": True, "label": "Current snapshot",
                "new_rule": "first available within the last %d days" % SNAPSHOT_NEW_DAYS}
    if t in ("today",):
        return {"start": today, "end": today, "label": "today", "days": 1}
    m = re.match(r"(?:last|past)\s+(\d+)\s*(day|days|week|weeks|month|months)$", t)
    if m:
        n = int(m.group(1)) * {"day": 1, "week": 7, "month": 30}[m.group(2).rstrip("s")]
        return {"start": today - _dt.timedelta(days=n - 1), "end": today, "label": "last %d days" % n, "days": n}
    m = re.match(r"(\d{4}-\d{2}-\d{2})\s*(?:to|-|–|until|\.\.)\s*(\d{4}-\d{2}-\d{2})$", t)
    if m:
        a, b = (_dt.date.fromisoformat(x) for x in m.groups())
        if b < a:
            a, b = b, a
        return {"start": a, "end": b, "label": "%s to %s" % (a, b), "days": (b - a).days + 1}
    raise ValueError("Unrecognised period '%s'. Use 'last 7 days', 'last 30 days', 'today' or 'YYYY-MM-DD to YYYY-MM-DD'." % text)


def parse_category(text):
    t = (text or "").strip().lower()
    if not t:
        raise ValueError("category missing")
    if t in ("all", "everything", "any"):
        return {"label": "all", "categories": list(ALL_CATEGORIES), "subcategory": None}
    parent, _, sub = t.partition("/")
    parent = parent.strip()
    if parent in CATEGORIES:
        return {"label": t, "categories": [parent], "subcategory": sub.strip() or None}
    for k, v in CATEGORIES.items():  # e.g. "car accessories" or "car electronics" -> free-text subcategory
        if parent in v["alibaba"] or parent.rstrip("s") in k:
            return {"label": t, "categories": [k], "subcategory": None if parent.rstrip("s") in k else parent}
    return {"label": t, "categories": [], "subcategory": parent}


def split_total(total):
    return {"top": (total + 1) // 2, "new": total // 2}


def resolve_target(cfg, text):
    t = (text or "").strip().lower()
    if t in ("worldwide", "global", "world", "ww", "all countries"):
        return "WW", "Worldwide"
    from .config import resolve_country
    code = resolve_country(cfg, t)
    return code, cfg["countries"][code]["name"]


def storefronts_for(cfg, code):
    over = (cfg.get("catalog") or {}).get("storefronts", {})
    return over.get(code) or STOREFRONTS.get(code) or STOREFRONTS["WW"]


# ---------------------------------------------------------------------------
# planning
# ---------------------------------------------------------------------------
def amazon_list_url(marketplace, kind, slug, page):
    path = "bestsellers" if kind == "bestseller" else "new-releases"
    q = []
    if page > 1:
        q.append("pg=%d" % page)
    if ENGLISH_LANG.get(marketplace):
        q.append("language=" + ENGLISH_LANG[marketplace])
    return "https://%s/gp/%s/%s%s" % (marketplace, path, slug, ("?" + "&".join(q)) if q else "")


def static_plan(params, cfg):
    """Ordered discovery tasks. Dynamic Alibaba-derived subcategory tasks are added during the run."""
    tasks = []
    cats = params["category"]["categories"]
    sub = params["category"]["subcategory"]
    fronts = storefronts_for(cfg, params["target"])
    for page in (1, 2):
        for cat in cats:
            c = CATEGORIES[cat]
            for mk, region, rel in fronts:
                if sub:
                    if page == 1:
                        tasks.append({"id": "amz-search-%s-%s" % (mk, sub), "source": "amazon", "kind": "keyword", "marketplace": mk,
                                      "region": region, "relation": rel, "list_type": "category_search", "keyword": "%s %s" % (sub, cat),
                                      "category": cat, "page": 1})
                    continue
                for kind in ("new_release", "bestseller"):
                    tasks.append({"id": "amz-%s-%s-%s-p%d" % (kind, mk, cat, page), "source": "amazon", "kind": "listing", "marketplace": mk,
                                  "region": region, "relation": rel, "list_type": kind, "category": cat, "page": page,
                                  "url": amazon_list_url(mk, kind, amazon_slug(cat, mk), page)})
            q = ("%s %s" % (sub, c["alibaba"])) if sub else c["alibaba"]
            tasks.append({"id": "ali-%s-p%d" % (cat, page), "source": "alibaba", "kind": "listing", "marketplace": "www.alibaba.com",
                          "region": "GLOBAL-B2B", "relation": "global wholesale marketplace (supplier offers, not retail)",
                          "list_type": "category_search", "category": cat, "page": page,
                          "url": "https://www.alibaba.com/trade/search?SearchText=%s%s" % (q.replace(" ", "+"), "&page=%d" % page if page > 1 else "")})
            if page == 1:
                for sp in c.get("specialist", []):
                    tasks.append({"id": "spec-%s-%s" % (sp["id"], cat), "source": "specialist", "kind": "keyword", "marketplace": sp["marketplace"],
                                  "region": "specialist", "relation": sp["label"], "list_type": "specialist_search", "category": cat,
                                  "keyword": sp["query"], "page": 1, "label": sp["label"]})
    if not cats:  # free-text category: search Amazon + Alibaba with the member's words
        for mk, region, rel in fronts:
            tasks.append({"id": "amz-search-%s-%s" % (mk, sub), "source": "amazon", "kind": "keyword", "marketplace": mk, "region": region,
                          "relation": rel, "list_type": "category_search", "keyword": sub, "category": sub, "page": 1})
        tasks.append({"id": "ali-%s-p1" % sub, "source": "alibaba", "kind": "listing", "marketplace": "www.alibaba.com", "region": "GLOBAL-B2B",
                      "relation": "global wholesale marketplace (supplier offers, not retail)", "list_type": "category_search", "category": sub,
                      "page": 1, "url": "https://www.alibaba.com/trade/search?SearchText=%s" % sub.replace(" ", "+")})
    return tasks


def payload_for(task, n):
    base = {"maxProductResults": n, "scrapeMode": "AUTO", "additionalProperties": True}
    if task["kind"] == "listing":
        return dict(base, listingUrls=[{"url": task["url"]}])
    return dict(base, keyword=task["keyword"], marketplaces=[task["marketplace"]])


# ---------------------------------------------------------------------------
# evidence extraction
# ---------------------------------------------------------------------------
RANK_RE = re.compile(r"ref=zg_(bs|bsnr|mw|mg)_(?:g_)?([A-Za-z0-9_-]+?)_d_sccl_(\d+)")
AR_MONTHS = {"يناير": 1, "فبراير": 2, "مارس": 3, "أبريل": 4, "ابريل": 4, "إبريل": 4, "مايو": 5, "يونيو": 6, "يوليو": 7,
             "أغسطس": 8, "اغسطس": 8, "سبتمبر": 9, "أكتوبر": 10, "اكتوبر": 10, "نوفمبر": 11, "ديسمبر": 12}
EN_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
DATE_KEYS = re.compile(r"date first available|date first listed|تاريخ توفر أول منتج|تاريخ أول توفر|تاريخ التوفر الأول|first available", re.I)
PACK_KEYS = re.compile(r"number of (items|pieces)|unit count|included components|عدد القطع|عدد العناصر|المكونات المضمنة|عدد الوحدات|عدد العبوات", re.I)
VARIANT_KEYS = re.compile(r"^(colou?r|size|style|pattern|اللون|المقاس|الحجم|النمط)$", re.I)
CLEAN = re.compile(r"[‎‏‪-‮]")


def clean(s):
    return CLEAN.sub("", str(s)).strip(" : ") if s is not None else None


def parse_listing_date(raw):
    """Parse 'November 6, 2026', '6 Nov. 2026', '6 November 2026', '2026 نوفمبر 6', '6 نوفمبر 2026', ISO dates."""
    if not raw:
        return None
    s = clean(raw)
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return _dt.date(*map(int, m.groups()))
    nums = [int(x) for x in re.findall(r"\d+", s)]
    month = None
    for name, i in AR_MONTHS.items():
        if name in s:
            month = i
    if month is None:
        for w in re.findall(r"[A-Za-z]+", s):
            if w[:3].lower() in EN_MONTHS:
                month = EN_MONTHS[w[:3].lower()]
                break
    years = [n for n in nums if n > 1900]
    days = [n for n in nums if 1 <= n <= 31]
    if month and years and days:
        try:
            return _dt.date(years[0], month, days[0])
        except ValueError:
            return None
    return None


def _strip_html(h):
    if not h:
        return None, []
    feats = [clean(html.unescape(re.sub(r"<[^>]+>", " ", li))) for li in re.findall(r"<li[^>]*>(.*?)</li>", h, re.S)]
    text = clean(html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h))))
    return text, [f for f in feats if f]


TOP_RANK_LIMIT = 100  # Amazon Best Sellers lists show the top 100 per category; deeper ranks are not "top selling"
SALES_WINDOW_RE = re.compile(r"(past|last)\s+(month|30 days|week|7 days|day|24 hours)", re.I)
SALES_WINDOW_DAYS = {"month": 30, "30 days": 30, "week": 7, "7 days": 7, "day": 1, "24 hours": 1}


def _kv_list(v):
    out = {}
    for e in v or []:
        if isinstance(e, dict):
            k = e.get("key") or e.get("name")
            if k and str(e.get("value") or "").strip():
                out[clean(k)] = clean(e.get("value"))
    return out


def _money(v):
    if isinstance(v, dict) and v.get("value") is not None:
        return {"value": v.get("value"), "currency": v.get("currency")}
    return None


def extract(row, task, observed_at):
    """Everything the marketplace returned, plus ranking / listing-date / sales evidence with its source."""
    props = row.get("additionalProperties") if isinstance(row.get("additionalProperties"), dict) else {}
    specs = {}
    for src in (props.get("extraProperties"), props.get("attributes"), props.get("productOverview"), props.get("manufacturerAttributes")):
        specs.update(_kv_list(src))
    desc_text, feats = _strip_html(props.get("descriptionHtml"))
    if isinstance(props.get("features"), list):
        feats = [clean(f) for f in props["features"] if f] or feats
    description = clean(row.get("description")) or desc_text
    if not feats and description:
        feats = [clean(x) for x in re.split(r"\n+", description) if x.strip()][1:12]
    offers = row.get("offers") if isinstance(row.get("offers"), dict) else {}
    mk = row.get("marketplace") or task["marketplace"]
    currency = offers.get("priceCurrency") or props.get("currencyRaw")
    currency_source = "reported by marketplace" if currency else None
    if not currency and mk in STOREFRONT_CURRENCY:
        currency, currency_source = STOREFRONT_CURRENCY[mk], "inferred from storefront %s" % mk
    url = row.get("url") or ""
    rank = None
    m = RANK_RE.search(url)
    if m:
        rank = {"list": {"bs": "Best Sellers", "bsnr": "New Releases", "mw": "Most Wished For", "mg": "Most Gifted"}[m.group(1)],
                "rank": int(m.group(3)), "rank_category": m.group(2).replace("-", " ").title(), "evidence_url": url,
                "list_url": task.get("url"), "window": BESTSELLER_WINDOW if m.group(1) == "bs" else NEW_RELEASE_WINDOW,
                "observed_at": observed_at, "source": "ranking page"}
    page_ranks = []
    for r in props.get("bestsellerRanks") or []:
        if isinstance(r, dict) and r.get("rank") is not None:
            try:
                page_ranks.append({"list": "Best Sellers", "rank": int(str(r["rank"]).replace(",", "")), "rank_category": clean(r.get("category")),
                                   "evidence_url": r.get("url") or url, "window": BESTSELLER_WINDOW, "observed_at": observed_at,
                                   "source": "product page 'Best Sellers Rank'"})
            except ValueError:
                pass
    sales_text = clean(props.get("monthlyPurchaseVolume") or row.get("monthlyPurchaseVolume") or props.get("salesVolume"))
    sales = None
    if sales_text:
        w = SALES_WINDOW_RE.search(sales_text)
        num = re.search(r"([\d,.]+)\s*([KkMm]?)\+?", sales_text)
        n = None
        if num:
            n = float(num.group(1).replace(",", "")) * {"k": 1e3, "m": 1e6}.get(num.group(2).lower(), 1)
        sales = {"text": sales_text, "min_units": int(n) if n else None, "window_days": SALES_WINDOW_DAYS.get(w.group(2).lower()) if w else None,
                 "window_text": w.group(0) if w else None, "observed_at": observed_at, "evidence_url": url}
    date_key = next((k for k in specs if DATE_KEYS.search(k or "")), None)
    date_raw = specs.get(date_key) if date_key else None
    listing_date = parse_listing_date(date_raw)
    pack = {k: v for k, v in specs.items() if PACK_KEYS.search(k or "")}
    variants = {k: v for k, v in specs.items() if VARIANT_KEYS.search(k or "")}
    variants.update(_kv_list(props.get("variantAttributes")))
    if props.get("color"):
        variants.setdefault("color", props["color"])
    variant_options = [clean(v.get("name")) for v in (props.get("variantDetails") or []) if isinstance(v, dict) and v.get("name")]
    images = []
    for src in (props.get("highResolutionImages"), props.get("images"), props.get("galleryThumbnails")):
        for i in src or []:
            u = i.get("url") if isinstance(i, dict) else i
            if u and u not in images:
                images.append(u)
    brand = row.get("brand") if isinstance(row.get("brand"), dict) else {"name": row.get("brand")}
    is_ali = "alibaba.com" in mk
    seller = props.get("seller") if isinstance(props.get("seller"), dict) else {}
    ids = {k: v for k, v in {"asin": props.get("asin") or (re.search(r"/dp/([A-Z0-9]{10})", url) or [None, None])[1],
                              "sku": props.get("sku"), "mpn": props.get("mpn") or row.get("mpn"), "gtin": row.get("gtin") or row.get("gtin13"),
                              "upc": specs.get("UPC")}.items() if v}
    if is_ali and props.get("sku"):
        ids["alibaba_product_id"] = props["sku"]
    rating = row.get("rating") if row.get("rating") is not None else props.get("stars")
    reviews = row.get("reviewCount")
    try:
        rating = float(rating) if rating not in (None, "") else None
    except (TypeError, ValueError):
        rating = None
    try:
        reviews = int(str(reviews).replace(",", "")) if reviews not in (None, "") else None
    except ValueError:
        reviews = None
    breadcrumbs = clean(props.get("breadCrumbs"))
    return {
        "source": task["source"], "task_id": task["id"], "marketplace": mk, "storefront_region": task["region"],
        "region_relation": task["relation"], "list_type": task["list_type"], "category_requested": task["category"],
        "found_by": {"query": task.get("keyword"), "page": task.get("url"), "list_type": task["list_type"], "marketplace": task["marketplace"]},
        "marketplace_category": " > ".join(props.get("categories") or []) or breadcrumbs or (rank["rank_category"] if rank else None),
        "title": clean(re.sub(r"\s*-\s*Buy\s+Product on Alibaba\.com$", "", row.get("name") or "")),
        "url": url, "image": row.get("image"), "images": images,
        "description": description, "features": feats, "specifications": specs, "variants": variants, "variant_options": variant_options,
        "pack_contents": pack, "brand": clean(brand.get("name") or brand.get("slogan")) if brand else None,
        "seller": clean(brand.get("name")) if is_ali else clean(seller.get("name") or (props.get("visitStoreLink") or {}).get("text")
                                                                if isinstance(props.get("visitStoreLink"), dict) else seller.get("name")),
        "supplier": {"name": clean(brand.get("name")), "moq": None, "price_tiers": None,
                     "note": "MOQ and price tiers were not returned by the scraper"} if is_ali else None,
        # a price of 0 (or below) is a marketplace placeholder, not a real price: keep it blank so it sorts last
        "price": offers.get("price") if isinstance(offers.get("price"), (int, float)) and offers.get("price") > 0 else None,
        "price_text": offers.get("priceText"), "currency": currency, "currency_source": currency_source,
        "price_label": "wholesale unit price (Alibaba; single figure returned, no quantity tiers)" if is_ali else "retail price",
        "list_price": _money(props.get("listPrice")),
        "price_range": {"min": _money((props.get("priceRange") or {}).get("min")), "max": _money((props.get("priceRange") or {}).get("max"))}
        if props.get("priceRange") else None,
        "rating": rating, "reviews": reviews, "sales": sales, "page_ranks": page_ranks,
        "stock": clean(props.get("inStockText")) or offers.get("availability") or props.get("availability"),
        "shipping": clean(props.get("delivery")) or (("shipping %s %s" % (props["shippingPrice"].get("currency"), props["shippingPrice"].get("value")))
                                                     if isinstance(props.get("shippingPrice"), dict) else None),
        "identifiers": ids, "rank": rank,
        "listing_date": listing_date.isoformat() if listing_date else None, "listing_date_raw": date_raw,
        "listing_date_source": ("%s product page field '%s'" % (mk, date_key) if date_raw else None),
        "observed_at": observed_at,
    }


# ---------------------------------------------------------------------------
# selection
# ---------------------------------------------------------------------------
def rank_window_fits(window, period):
    return top_period_ok(period) and window == BESTSELLER_WINDOW


def top_period_ok(period):
    """Current rankings match the selected period only for a Current snapshot (or a 'today' range)."""
    return bool(period.get("snapshot")) or (period["days"] == 1 and period["end"] == utcnow().date())


def sales_window_fits(sales, period):
    """A sales figure matches the period when its reported window lies inside it (e.g. 'past month' within 'last 30 days')."""
    if not sales or not sales.get("window_days"):
        return False
    obs = _dt.date.fromisoformat(sales["observed_at"][:10])
    start = obs - _dt.timedelta(days=sales["window_days"] - 1)
    return period["start"] <= start and obs <= period["end"] + _dt.timedelta(days=1)


def classify(con, job_id, period):
    """Per distinct product: evidence, view membership (Top Selling / Newly Listed / Unmatched) and period matching."""
    prods = {}
    for r in con.execute("""SELECT ce.product_id, ce.evidence, jp.relation, jp.uncertain_of
                            FROM catalog_evidence ce JOIN job_products jp ON jp.product_id=ce.product_id AND jp.job_id=ce.job_id
                            WHERE ce.job_id=? ORDER BY ce.batch_id, ce.row_index""", (job_id,)):
        p = prods.setdefault(r["product_id"], {"product_id": r["product_id"], "relation": r["relation"],
                                               "uncertain_of": r["uncertain_of"], "evidence": []})
        p["evidence"].append(json.loads(r["evidence"]))
    start, end = period["start"].isoformat(), period["end"].isoformat()
    for p in prods.values():
        evs = p["evidence"]
        list_ranks = [dict(e["rank"], marketplace=e["marketplace"]) for e in evs if e.get("rank") and e["rank"]["list"] == "Best Sellers"]
        page_ranks = [dict(r, marketplace=e["marketplace"]) for e in evs for r in (e.get("page_ranks") or []) if r["rank"] <= TOP_RANK_LIMIT]
        ranks = sorted(list_ranks + page_ranks, key=lambda r: r["rank"])
        p["ranks"] = ranks
        p["best_rank"] = ranks[0] if ranks else None
        p["all_page_ranks"] = [dict(r, marketplace=e["marketplace"]) for e in evs for r in (e.get("page_ranks") or [])]
        sales = [dict(e["sales"], marketplace=e["marketplace"]) for e in evs if e.get("sales")]
        p["sales"] = max(sales, key=lambda s: s.get("min_units") or 0) if sales else None
        p["rank_match"] = bool(ranks) and top_period_ok(period)
        p["sales_match"] = bool(p["sales"]) and sales_window_fits(p["sales"], period)
        p["top_any"] = bool(ranks or p["sales"])
        p["top_verified"] = p["rank_match"] or p["sales_match"]
        p["current_bestseller"] = bool(ranks) and not p["top_verified"]
        dates = [e for e in evs if e.get("listing_date")]
        d = dates[0] if dates else None
        p["listing"] = d
        p["new_verified"] = bool(d and start <= d["listing_date"] <= end)
        p["new_release_rank"] = next((dict(e["rank"], marketplace=e["marketplace"]) for e in evs
                                      if e.get("rank") and e["rank"]["list"] == "New Releases"), None)
        p["new_undated"] = bool(p["new_release_rank"]) and not d
        p["new_outside"] = bool(d) and not p["new_verified"]
        p["new_any"] = p["new_verified"] or p["new_undated"]
        p["unmatched"] = not p["top_any"] and not p["new_any"]
        p["possible_duplicate"] = p["relation"] == "uncertain"
        badges = ["%s #%d in %s (%s)" % (r["list"], r["rank"], r["rank_category"], r["marketplace"]) for r in ranks]
        if p["new_release_rank"]:
            badges.append("New Releases #%d (%s)" % (p["new_release_rank"]["rank"], p["new_release_rank"]["marketplace"]))
        if d:
            badges.append("First available %s (%s)" % (d["listing_date"], d["marketplace"]))
        if p["sales"]:
            badges.append("%s (%s)" % (p["sales"]["text"], p["sales"]["marketplace"]))
        p["badges"] = sorted(set(badges))
        p["sources"] = sorted({e["marketplace"] for e in evs})
        p["primary"] = next((e for e in evs if e.get("rank") or e.get("page_ranks")), None) or (d or evs[0])
    return prods


def select(prods, total):
    """Verified counts for the requested target. Products eligible for both groups fill the scarcer group; counted once."""
    q = split_total(total)
    eligible = [p for p in prods.values() if p["relation"] == "new"]
    new_pool = sorted([p for p in eligible if p["new_verified"]], key=lambda p: p["listing"]["listing_date"], reverse=True)
    top_pool = sorted([p for p in eligible if p["top_verified"]],
                      key=lambda p: (p["best_rank"]["rank"] if p["best_rank"] else 10 ** 6, -((p["sales"] or {}).get("min_units") or 0)))
    chosen, out = set(), {"top": [], "new": []}
    for group, pool in sorted((("top", top_pool), ("new", new_pool)), key=lambda g: len(g[1])):
        for p in pool:
            if len(out[group]) >= q[group]:
                break
            if p["product_id"] not in chosen:
                out[group].append(p)
                chosen.add(p["product_id"])
    rest = [p for p in eligible if p["product_id"] not in chosen]
    current = sorted([p for p in rest if p["current_bestseller"]], key=lambda p: p["best_rank"]["rank"])
    undated = sorted([p for p in rest if p["new_undated"]], key=lambda p: p["new_release_rank"]["rank"])
    shown_ids = {p["product_id"] for p in current[: q["top"]] + undated[: q["new"]]} | chosen
    return {"quota": q, "groups": out, "current_bestsellers": current, "new_undated": undated,
            "extras": [p for p in eligible if p["product_id"] not in shown_ids], "seen_before": [p for p in prods.values() if p["relation"] != "new"]}


def reextract(con, job_id):
    """Rebuild stored evidence from the saved raw rows with the current extractor. Free: no API calls."""
    cp = db.unj(con.execute("SELECT checkpoint FROM jobs WHERE id=?", (job_id,)).fetchone()[0], {}) or {}
    tasks = cp.get("task_by_batch", {})
    n = 0
    rows = con.execute("""SELECT ce.batch_id, ce.row_index, ce.evidence, rr.payload FROM catalog_evidence ce
                          JOIN raw_rows rr ON rr.batch_id=ce.batch_id AND rr.row_index=ce.row_index WHERE ce.job_id=?""", (job_id,)).fetchall()
    con.execute("BEGIN IMMEDIATE")
    try:
        for r in rows:
            task = tasks.get(r["batch_id"])
            if not task:
                continue
            old = json.loads(r["evidence"])
            ev = extract(json.loads(r["payload"]), task, old.get("observed_at") or db.now())
            con.execute("UPDATE catalog_evidence SET evidence=? WHERE batch_id=? AND row_index=?", (db.j(ev), r["batch_id"], r["row_index"]))
            n += 1
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return n



# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------
class CatalogRunner(JobRunner):
    def params(self):
        p = dict(self.checkpoint()["params"])
        p["period"] = parse_period(p["period_text"], _dt.date.fromisoformat(p["requested_on"]))
        p["category"] = parse_category(p["category_text"])
        return p

    def estimate(self, n):
        if self.job["mode"] == "fixture":
            return 0.0
        return round(self.cfg["apify"]["estimated_cost"]["per_run_start_usd"] + self.cfg["apify"]["estimated_cost"]["per_product_usd"] * n, 4)

    def plan(self):
        """Ranking pages first (Amazon Best Sellers / New Releases, Alibaba category page, specialists), then the keyword
        planner: localized seeds per subcategory + on-category expansions from returned results, ordered for variety."""
        from . import queries
        p = self.params()
        tasks = static_plan(p, self.cfg)
        cp = self.checkpoint()
        fronts = storefronts_for(self.cfg, p["target"])
        n = self.cfg["discovery"].get("products_per_batch", 40)
        qtasks = []
        for cat in p["category"]["categories"] or [p["category"]["subcategory"]]:
            qtasks += queries.seeds(cat, p["target"], fronts, p["category"]["subcategory"] if p["category"]["categories"] else None)
            qtasks += queries.expansions(self.con, self.job_id, cat, fronts)
        stats = queries.query_stats(self.con, self.job_id, cp.get("task_by_batch", {}))
        if getattr(self, "_ran_keys", None) is None:
            self._ran_keys = queries.already_run_keys(self.con, self.job["country"], self.job["mode"])
        ordered, tired = queries.order(qtasks, stats, set(cp.get("tasks_done", [])), self._ran_keys, n,
                                       self.cfg["discovery"].get("low_yield_threshold", 0.15))
        if tired != cp.get("tired_subcategories"):
            cp["tired_subcategories"] = tired
            self.save_checkpoint(cp)
        return tasks + ordered

    def selection(self):
        p = self.params()
        return select(classify(self.con, self.job_id, p["period"]), p["total"]), p

    def first_pass_done(self):
        """Every search attempts Amazon, Alibaba and any specialist at least once before it may stop."""
        cp = self.checkpoint()
        done = set(cp.get("tasks_done", []))
        dead = cp.get("dead_sources", {})
        required = [t for t in static_plan(self.params(), self.cfg) if t["page"] == 1
                    and "%s:%s" % (t["marketplace"], t["list_type"]) not in dead]
        return all(t["id"] in done for t in required)

    def useful_filter(self):
        """After the first pass, only spend on sources that can still fill an unfilled group.
        Alibaba and specialist pages return no ranking or listing date, so they stop after the first pass."""
        if not self.first_pass_done():
            return lambda t: True
        sel, p = self.selection()
        q = sel["quota"]
        need_new = len(sel["groups"]["new"]) < q["new"]
        need_top = len(sel["groups"]["top"]) < q["top"] and top_period_ok(p["period"])
        # Amazon search results can carry "N bought in past month", which can match a date range
        need_top_sales = len(sel["groups"]["top"]) < q["top"]
        cap = self.cfg["discovery"].get("max_query_searches", 8)
        used = sum(1 for t in self.checkpoint().get("tasks_done", []) if t.startswith(("amz-q-", "amz-x-", "amz-sub-")))

        ali_cap = self.cfg["discovery"].get("max_alibaba_queries", 2)
        ali_used = sum(1 for t in self.checkpoint().get("tasks_done", []) if t.startswith(("ali-q-", "ali-x-")))

        def ok(t):
            if t["source"] == "alibaba" and t["id"].startswith(("ali-q-", "ali-x-")):
                return ali_used < ali_cap  # supplier-style discovery for browsing; Alibaba returns no rank/date
            if t["source"] != "amazon":
                return False
            if t["list_type"] == "new_release":
                return need_new
            if t["list_type"] == "bestseller":
                return need_top
            if t["id"].startswith(("amz-q-", "amz-x-", "amz-sub-")):
                return (need_new or need_top_sales) and used < cap
            return need_new
        return ok

    def done_enough(self):
        if not self.first_pass_done():
            return False
        sel, p = self.selection()
        q = sel["quota"]
        new_ok = len(sel["groups"]["new"]) >= q["new"]
        top_possible = top_period_ok(p["period"])
        top_ok = len(sel["groups"]["top"]) >= q["top"] or (not top_possible and len(sel["current_bestsellers"]) >= q["top"])
        return new_ok and top_ok

    def dispatch_catalog(self):
        cp = self.checkpoint()
        done_ids = set(cp.get("tasks_done", []))
        dead = cp.get("dead_sources", {})
        n = self.cfg["discovery"].get("products_per_batch", 40)
        while self.in_flight() < self.cfg["budget"]["max_concurrent_runs"]:
            useful = self.useful_filter()
            task = next((t for t in self.plan() if t["id"] not in done_ids
                         and "%s:%s" % (t["marketplace"], t["list_type"]) not in dead and useful(t)), None)
            if task is None:
                return "sources_exhausted" if self.in_flight() == 0 else None
            payload = payload_for(task, n)
            try:
                apify.validate_input(payload, self.schema()[1])
            except apify.SchemaError as e:
                self.log("Skipping %s: %s" % (task["id"], e), "warn")
                done_ids.add(task["id"])
                cp["tasks_done"] = sorted(done_ids)
                self.save_checkpoint(cp)
                continue
            est = self.estimate(n)
            ok, why = self.budget_allows(est)
            if not ok:
                return ("budget_exhausted: " + why) if self.in_flight() == 0 else None
            bid = self.create_batch("catalog", task["id"], payload, est, category_id=task["list_type"],
                                    term=task.get("url") or task.get("keyword"), lang=task["marketplace"])
            done_ids.add(task["id"])
            cp["tasks_done"] = sorted(done_ids)
            cp.setdefault("task_by_batch", {})[bid] = task
            self.save_checkpoint(cp)
            self.submit(bid)
        return None

    def ingest_page(self, b, items):
        cp = self.checkpoint()
        task = cp["task_by_batch"][b["id"]]
        m = self.matcher()
        new_count = 0
        try:
            self.con.execute("BEGIN IMMEDIATE")
            for i, row in enumerate(items):
                idx = b["ingest_offset"] + i
                if self.con.execute("INSERT OR IGNORE INTO raw_rows VALUES (?,?,?,?)", (b["id"], idx, self.job_id, db.j(row))).rowcount == 0:
                    continue
                self.con.execute("SAVEPOINT row_sp")
                try:
                    new_count += self.place_row(b, idx, row, task, cp, m)
                    self.con.execute("RELEASE row_sp")
                except Exception as e:  # noqa: BLE001 - quarantine one bad record, keep ingesting
                    self.con.execute("ROLLBACK TO row_sp")
                    self.con.execute("RELEASE row_sp")
                    self._matcher = None  # in-memory index may hold the rolled-back product
                    m = self.matcher()
                    cp.setdefault("unparsed_rows", []).append({"batch": b["id"], "row": idx, "error": str(e)[:200]})
                    db.log(self.con, self.job_id, "Row %s/%d kept raw but not parsed: %s" % (b["id"], idx, e), "warn")
            self.con.execute("UPDATE batches SET ingest_offset=ingest_offset+?, new_products=new_products+?, updated_at=? WHERE id=?",
                             (len(items), new_count, db.now(), b["id"]))
            self.con.execute("UPDATE jobs SET checkpoint=?, updated_at=? WHERE id=?", (db.j(cp), db.now(), self.job_id))
            self.con.execute("COMMIT")
        except Exception:
            self.con.execute("ROLLBACK")
            self._matcher = None
            raise

    def place_row(self, b, idx, row, task, cp, m):
        ev = extract(row, task, db.now())
        if not ev["title"]:
            return 0
        off = dedupe.normalize(dict(row, name=ev["title"]), task["id"], 0, ev["observed_at"])
        off["currency"] = ev["currency"]
        if ev["identifiers"].get("asin"):
            off["identifiers"]["asin"] = ev["identifiers"]["asin"]
        b2 = dict(b)
        b2["term"], b2["lang"], b2["source_id"] = task.get("keyword") or task["list_type"], task["marketplace"], task["id"]
        new = self.place_offer(b2, idx * 100, off, None, m)
        pid = self.con.execute("SELECT product_id FROM offers WHERE batch_id=? AND row_index=?", (b["id"], idx * 100)).fetchone()[0]
        self.con.execute("INSERT OR REPLACE INTO catalog_evidence VALUES (?,?,?,?,?)", (b["id"], idx, self.job_id, pid, db.j(ev)))
        if task["source"] == "alibaba" and ev["marketplace_category"]:
            leaf = ev["marketplace_category"].split(" > ")[-1].lower()
            subs = cp.setdefault("discovered_subcategories", [])
            if leaf and leaf not in subs and len(subs) < 30:
                subs.append(leaf)
        return new

    def mark_dead_sources(self):
        """Record which sources returned data; a list type returning nothing twice is switched off for this job."""
        cp = self.checkpoint()
        cov = {}
        for b in self.con.execute("SELECT * FROM batches WHERE job_id=? AND purpose='catalog' ORDER BY seq", (self.job_id,)):
            t = cp["task_by_batch"].get(b["id"], {})
            key = "%s:%s" % (t.get("marketplace"), t.get("list_type"))
            c = cov.setdefault(key, {"marketplace": t.get("marketplace"), "list_type": t.get("list_type"), "source": t.get("source"),
                                     "relation": t.get("relation"), "runs": 0, "finished": 0, "rows": 0, "urls": [], "empty_streak": 0})
            c["runs"] += 1
            if b["status"] in ("ingested", "failed"):
                c["finished"] += 1
                c["rows"] += b["ingest_offset"]
                c["empty_streak"] = 0 if b["ingest_offset"] else c["empty_streak"] + 1
            if t.get("url") or t.get("keyword"):
                c["urls"].append(t.get("url") or "keyword: %s" % t.get("keyword"))
        dead = cp.get("dead_sources", {})
        for key, c in cov.items():
            limit = 1 if c["source"] == "specialist" else 2
            if key not in dead and c["empty_streak"] >= limit:
                dead[key] = "returned no data in %d run(s)" % c["empty_streak"]
                self.log("Source %s switched off: %s" % (key, dead[key]), "warn")
        cp["coverage"], cp["dead_sources"] = cov, dead
        self.save_checkpoint(cp)

    def step(self):
        job = self.job
        if job["status"] == "queued":
            self.set(status="running")
        try:
            if job["phase"] == "discovery":
                self.poll()
                self.ingest()
                self.mark_dead_sources()
                if self.done_enough():
                    if self.in_flight() == 0 and not self.con.execute("SELECT 1 FROM batches WHERE job_id=? AND status='succeeded'", (self.job_id,)).fetchone():
                        sel, p = self.selection()
                        full = len(sel["groups"]["top"]) >= sel["quota"]["top"]
                        self.set(stop_reason="both groups filled" if full else
                                 "Newly Listed filled; Top Sellers: sources only report a current ranking, not a %s ranking" % p["period"]["label"],
                                 phase="report")
                    return
                stop = self.dispatch_catalog()
                if stop and self.in_flight() == 0 and not self.con.execute(
                        "SELECT 1 FROM batches WHERE job_id=? AND status='succeeded'", (self.job_id,)).fetchone():
                    self.set(stop_reason=stop, phase="report")
            elif job["phase"] == "report":
                from . import catalog_report
                sel, p = self.selection()
                q = sel["quota"]
                complete = len(sel["groups"]["top"]) >= q["top"] and len(sel["groups"]["new"]) >= q["new"]
                self.set(status="complete" if complete else "partial")
                out = catalog_report.generate(self.con, self.cfg, self.env.paths, self.job_id)
                self.set(report_dir=out, phase="done")
                self.log("Catalog report written to %s" % out)
        except apify.ApifyError as e:
            if "APIFY_TOKEN" in str(e):
                self.set(stop_reason="credentials_missing: APIFY_TOKEN not configured", phase="report")
                return
            self.log("Apify error: %s" % e, "error")
