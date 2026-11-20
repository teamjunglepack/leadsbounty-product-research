"""Offline fixture providers for the free demo and tests. Nothing here touches the network.
All fixture data is synthetic and every fixture report is labelled FIXTURE DEMO."""
import base64
import hashlib
import json
import os

from . import apify
from .config import DATA_DIR

# (category, title, local_usd, supplier_usd, flags)
CATALOG = [
    ("home-organization", "Expandable Drawer Organizer 6 Grids Bamboo", 34, 6.5, ""),
    ("home-organization", "Vacuum Storage Bags Set 8 Pack with Hand Pump", 31, 5.8, ""),
    ("home-organization", "Over Door Shoe Rack 5 Tier Metal", 24, 4.2, "cheap"),
    ("kitchen-gadgets", "Electric Vegetable Chopper 500ml USB Rechargeable", 39, 7.9, ""),
    ("kitchen-gadgets", "Olive Oil Sprayer Bottle 200ml Glass", 19, 1.6, "cheap"),
    ("kitchen-gadgets", "Handheld Milk Frother Stainless 3 Speed", 29, 3.9, ""),
    ("car-accessories", "Portable Tire Inflator 150PSI Digital Cordless", 59, 14.5, ""),
    ("car-accessories", "Car Vacuum Cleaner 120W Wireless High Suction", 45, 9.8, ""),
    ("car-accessories", "Car Seat Gap Filler Organizer Leather", 22, 2.9, "cheap"),
    ("beauty-tools", "Hair Straightener Brush Ceramic Ionic 230C", 42, 8.1, ""),
    ("beauty-tools", "Electric Facial Cleansing Brush Waterproof Silicone", 36, 5.2, "nosupplier"),
    ("beauty-tools", "Electric Nail Drill 20000RPM Manicure Kit", 33, 6.7, ""),
    ("health-massage", "Neck Massager Heated Shiatsu 8 Nodes", 55, 11.9, ""),
    ("health-massage", "Foot Massager Machine Heat Air Compression", 89, 27.0, ""),
    ("health-massage", "Posture Corrector Adjustable Back Brace", 27, 3.1, "cheap"),
    ("cleaning", "Electric Spin Scrubber Cordless 8 Brush Heads", 49, 10.5, ""),
    ("cleaning", "Lint Remover Rechargeable Fabric Shaver 6 Blades", 21, 3.3, "cheap"),
    ("cleaning", "Window Cleaning Robot Smart Suction", 129, 48.0, "broken_img"),
    ("baby-kids", "Electric Baby Nasal Aspirator Music 3 Levels", 35, 6.0, ""),
    ("baby-kids", "LCD Drawing Tablet Kids 12 Inch Colorful", 25, 2.8, "cheap"),
    ("baby-kids", "Portable Baby Bottle Warmer Wireless 6000mAh", 46, 9.2, ""),
    ("fitness", "Ab Roller Wheel Automatic Rebound Elbow Support", 32, 5.5, ""),
    ("fitness", "Resistance Bands Set 11 Pieces Door Anchor", 29, 4.4, ""),
    ("fitness", "Smart Jump Rope Digital Counter Cordless", 30, 4.9, ""),
    ("phone-accessories", "Magnetic Wireless Power Bank 10000mAh 15W", 38, 8.6, ""),
    ("phone-accessories", "Selfie Stick Tripod Bluetooth Remote 1.5m", 28, 3.6, "cheap"),
    ("phone-accessories", "Car Phone Holder Magnetic Dashboard 360", 18, 1.9, "cheap"),
    ("pet", "Pet Hair Remover Roller Reusable", 29, 2.7, ""),
    ("pet", "Automatic Pet Feeder 4L Timer Voice", 69, 17.5, ""),
    ("pet", "Cat Water Fountain 2.5L Silent Filter", 41, 7.8, ""),
    ("outdoor", "Rechargeable Camping Lantern 5000mAh Solar", 37, 7.1, ""),
    ("outdoor", "Bladeless Neck Fan 4000mAh 3 Speeds", 33, 5.9, ""),
    ("outdoor", "Insulated Water Bottle 1L Stainless Temperature Display", 26, 3.4, "cheap"),
    ("home-security", "WiFi Security Camera 4MP Pan Tilt Night Vision", 49, 13.0, ""),
    ("home-security", "Smart Fingerprint Door Lock Keyless", 119, 39.0, ""),
    ("home-security", "Motion Sensor Night Light LED 3 Pack", 23, 3.1, "cheap"),
]
SHARED = ("phone-accessories", "Magnetic Wireless Power Bank 10000mAh 15W", 38, 8.6, "")
DEMO_ECONOMICS = {"confirmation_rate": 0.7, "delivery_rate": 0.75, "delivery_fee_usd": 4.0, "return_cost_usd": 3.0,
                  "fulfillment_fee_usd": 1.5, "shipping_to_country_per_unit_usd": 3.0}
LOW_DEMAND_CATEGORIES = {"pet"}
COLORS = ["#e8590c", "#1971c2", "#2f9e44", "#9c36b5", "#c92a2a", "#0c8599", "#e67700", "#5f3dc4"]


def _asin(title):
    return "B0" + hashlib.sha1(title.encode()).hexdigest()[:8].upper()


def image_for(title, flags=""):
    if "broken_img" in flags:
        return "https://fixture.leadsbounty.invalid/BROKEN/%s.jpg" % _asin(title)
    color = COLORS[int(hashlib.sha1(title.encode()).hexdigest(), 16) % len(COLORS)]
    words = title.split()
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="240" height="240"><rect width="240" height="240" fill="%s"/>'
           '<text x="120" y="105" font-family="Arial" font-size="18" fill="#fff" text-anchor="middle">%s</text>'
           '<text x="120" y="130" font-family="Arial" font-size="18" fill="#fff" text-anchor="middle">%s</text>'
           '<text x="120" y="200" font-family="Arial" font-size="14" fill="#fff" text-anchor="middle" opacity=".8">FIXTURE</text></svg>'
           % (color, " ".join(words[:2]), " ".join(words[2:4])))
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


def fake_image_check(url):
    if not url:
        return False, "no image URL"
    if "BROKEN" in url:
        return False, "HTTP 404 (fixture broken image)"
    return True, "loads (fixture image)"


def _category_of_term(term):
    with open(os.path.join(DATA_DIR, "countries.json"), encoding="utf-8") as f:
        cats = json.load(f)["_categories"]
    for c in cats:
        for terms in c["terms"].values():
            if term in terms:
                return c["id"]
    return None


def _items_for(payload):
    if payload.get("listingUrls") or payload.get("additionalProperties"):
        return catalog_items_for(payload)
    if "searchEngineKeyword" in payload:
        cat = _category_of_term(payload["searchEngineKeyword"])
        rows = [p for p in CATALOG if p[0] == cat] + [SHARED]
        out = []
        for cat_, title, usd, sup, flags in rows[: payload.get("maxSearchEngineProducts", 10)]:
            gtitle = title + " Lebanon"
            if title.startswith("Lint Remover"):
                gtitle = "Lint Remover Fabric Shaver Electric"  # near-duplicate -> uncertain match
            out.append({"product": {"title": gtitle, "price": "$%d" % usd, "image": image_for(title, flags)},
                        "sellers": [{"merchantName": "Beirut Gadgets", "price": "$%d" % usd, "url": "https://shop-a.fixture.invalid/%s" % _asin(title)},
                                    {"merchantName": "Cedar Store", "price": "$%d" % (usd + 3), "url": "https://shop-b.fixture.invalid/%s" % _asin(title)}]})
        return out
    mk = (payload.get("marketplaces") or [""])[0]
    if mk == "www.alibaba.com":
        kw = set(payload["keyword"].lower().split())
        best = max(CATALOG, key=lambda p: len(kw & set(p[1].lower().split())))
        cat, title, usd, sup, flags = best
        stitle = title if "nosupplier" not in flags else title.replace("Electric", "Manual") + " 2 Pack"
        return [{"url": "https://www.alibaba.com/product-detail/fixture_%s.html" % _asin(title), "name": stitle + " Wholesale OEM",
                 "offers": {"price": sup, "priceCurrency": "USD"}, "image": image_for(title), "seller": "Shenzhen Fixture Co."},
                {"url": "https://www.alibaba.com/product-detail/fixture_alt_%s.html" % _asin(title), "name": "Assorted generic gadget 3 Pack",
                 "offers": {"price": round(sup * 0.6, 2), "priceCurrency": "USD"}, "image": image_for("alt " + title)}]
    cat = _category_of_term(payload.get("keyword", ""))
    rows = [p for p in CATALOG if p[0] == cat] + [SHARED]
    out = []
    for cat_, title, usd, sup, flags in rows[: payload.get("maxProductResults", 10)]:
        out.append({"url": "https://%s/dp/%s" % (mk, _asin(title)), "name": title,
                    "offers": {"price": round(usd * 3.67 * 0.9, 2), "priceCurrency": "AED"},
                    "image": image_for(title, flags), "description": "Fixture listing for %s" % title,
                    "additionalProperties": {"features": ["Synthetic fixture feature"]}})
        if title.startswith("Electric Vegetable Chopper"):
            out.append({"url": "https://%s/dp/%s" % (mk, _asin(title + "2")), "name": title + " 2 Pack",
                        "offers": {"price": round(usd * 1.8 * 3.67 * 0.9, 2), "priceCurrency": "AED"}, "image": image_for(title)})
    return out


class FakeApify:
    """Stateless across processes: run id = input hash, dataset is recomputed from the input."""

    def __init__(self, db_path=None):
        self.db_path = db_path
        with open(os.path.join(DATA_DIR, "actor_input_schema.json"), encoding="utf-8") as f:
            self._schema = json.load(f)["schema"]
        self.started = []

    def latest_build(self):
        return {"build_id": "fixture-build", "build_number": "fixture", "schema": self._schema, "pricing": None}

    def start_run(self, payload, build_number=None, max_charge_usd=None):
        rid = "fx-" + apify.input_hash(payload)[:16]
        self.started.append(rid)
        _RUNS[rid] = payload
        return {"id": rid, "defaultDatasetId": rid, "status": "RUNNING", "startedAt": "", "defaultKeyValueStoreId": rid}

    def _load(self, run_id):
        if run_id not in _RUNS and self.db_path:
            import sqlite3
            con = sqlite3.connect(self.db_path)
            row = con.execute("SELECT input_json FROM batches WHERE apify_run_id=?", (run_id,)).fetchone()
            con.close()
            if row:
                _RUNS[run_id] = json.loads(row[0])
        return _RUNS.get(run_id)

    def get_run(self, run_id):
        payload = self._load(run_id)
        items = _items_for(payload) if payload else []
        return {"id": run_id, "status": "SUCCEEDED", "defaultDatasetId": run_id, "usageTotalUsd": 0.0,
                "stats": {"itemCount": len(items)}}

    def find_run_for_input(self, payload, since_iso):
        rid = "fx-" + apify.input_hash(payload)[:16]
        return self.get_run(rid) if rid in _RUNS else None

    def dataset_items(self, dataset_id, offset, limit=200):
        payload = self._load(dataset_id)
        return _items_for(payload)[offset: offset + limit] if payload else []


_RUNS = {}  # fixture runs are rehydrated from the batches table after a restart


class FakeTrends:
    name = "fixture_trends"

    def fetch(self, query, geo, timeframe):
        h = int(hashlib.sha1(query.encode()).hexdigest(), 16)
        cat = _category_of_term(query)
        base = 8 if cat in LOW_DEMAND_CATEGORIES else 35 + h % 40
        timeline = [{"date": "2026-%02d" % m, "values": [{"extracted_value": max(0, base + (m % 4) * 3 - 4)}]} for m in range(1, 13)]
        return {"interest_over_time": {"timeline_data": timeline}, "fixture": True}


class FixtureCreative:
    name = "fixture-template"

    def generate(self, ctx):
        t = ctx["product"]["title"]
        obs = "; ".join(ctx["qualification"][:2])
        return [
            {"hook": "شو؟ بعدك عم تتعب؟ هيدا الحل!", "hook_translation": "What? Still struggling? This is the fix!",
             "demo": "Before/after: show the problem for 3 seconds, then %s solving it in one take." % t, "observation": obs},
            {"hook": "الدفع عند الاستلام، جربو ببيتك", "hook_translation": "Pay on delivery, try it at home",
             "demo": "Unboxing on a kitchen table showing every item in the pack.", "observation": ctx["qualification"][1] if len(ctx["qualification"]) > 1 else obs},
            {"hook": "ليش تدفع أكتر بالمحلات؟", "hook_translation": "Why pay more in stores?",
             "demo": "Side-by-side price card versus a local shop shelf, then the product in use.", "observation": ctx["qualification"][-1]},
        ]


# ---------------------------------------------------------------------------
# Catalog fixtures: Amazon Best Sellers / New Releases pages, Alibaba category search, empty specialist.
# Dates are relative to today so period tests stay stable.
# ---------------------------------------------------------------------------
import datetime as _dt  # noqa: E402
import re as _re  # noqa: E402

CAT_NOUNS = {
    "automotive": ["Car Phone Holder", "Tire Inflator", "Seat Gap Filler", "Dash Cam", "Car Vacuum", "Steering Wheel Cover",
                   "Windshield Sun Shade", "Jump Starter", "Trunk Organizer", "Vent Air Freshener"],
}
DEFAULT_NOUNS = ["Organizer", "Gadget", "Kit", "Tool", "Holder", "Lamp", "Brush", "Bottle", "Stand", "Case"]
ADJS = ["Magnetic", "Portable", "Premium", "Wireless", "Compact", "Foldable", "Smart", "Rechargeable", "Heavy Duty"]
COLORS2 = ["Black", "Grey", "Blue", "Red", "White"]


def _cat_from(text):
    for k in ("automotive", "hpc", "health", "kitchen", "beauty", "electronics", "home", "baby", "toys", "sporting", "pet"):
        if k in text:
            return "automotive" if k == "automotive" else k
    return "general"


def catalog_item(cat, k):
    nouns = CAT_NOUNS.get(cat, ["%s %s" % (cat.title(), n) for n in DEFAULT_NOUNS])
    noun, adj = nouns[k % 10], ADJS[(k // 10) % len(ADJS)]
    feat = ["with LED Light", "for SUV Sedan", "Pro Edition", "Travel Kit Set", "Dual Port USB", "Waterproof Shell",
            "Anti Slip Base", "Quick Install", "Ultra Thin Design"][(k // 10) % 9]
    title = "%s %s %s %s" % (adj, noun, feat, COLORS2[(k // 10) % 5])
    asin = "B0" + hashlib.sha1(("%s|%s|%s" % (cat, noun, adj)).encode()).hexdigest()[:8].upper()
    return title, asin


def _date_for(k):
    today = _dt.datetime.now(_dt.timezone.utc).date()
    mode = k % 4
    if mode == 0:
        return None
    days = {1: 3, 2: 20, 3: 60}[mode]
    d = today - _dt.timedelta(days=days)
    return "%s %d, %d" % (d.strftime("%B"), d.day, d.year) if k % 8 < 4 else "%d %s %d" % (
        d.year, ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"][d.month - 1], d.day)


def _amazon_row(mk, cat, k, list_code, rank, listing_url, with_date=True):
    title, asin = catalog_item(cat, k)
    extra = [{"name": "Manufacturer", "value": "Fixture Brand %d" % (k % 7)}, {"name": "Number of Items", "value": "1"},
             {"name": "Colour", "value": COLORS2[k % 5]}, {"name": "Item Weight", "value": "%d g" % (100 + k)}]
    date = _date_for(k) if with_date else None
    if date:
        extra.append({"name": "Date First Available" if "," in date else "تاريخ توفر أول منتج", "value": "‎ " + date})
    url = "https://%s/dp/%s/ref=%s_g_%s_d_sccl_%d/fixture?psc=1" % (mk, asin, list_code, cat, rank) if list_code else \
        "https://%s/dp/%s" % (mk, asin)
    return {"url": url, "name": title, "offers": {"price": 20 + (k * 7) % 180, "priceText": str(20 + (k * 7) % 180), "priceCurrency": None},
            "brand": {"name": None}, "image": image_for(title), "rating": round(3.5 + (k % 15) / 10, 1), "reviewCount": 10 * k + 3,
            "additionalProperties": {"sku": asin, "mpn": "FX-%d" % k, "images": [{"url": image_for(title)}],
                                     "descriptionHtml": "<article><h2>About this item</h2><ul><li>Fixture feature A for %s</li><li>Fixture feature B</li></ul></article>" % title,
                                     "color": COLORS2[k % 5], "extraProperties": extra},
            "inputUrl": listing_url, "marketplace": mk}


def catalog_items_for(payload):
    n = payload.get("maxProductResults", 20)
    if payload.get("listingUrls"):
        url = payload["listingUrls"][0]["url"]
        mk = url.split("/")[2]
        page = int((_re.search(r"pg=(\d+)", url) or [0, 1])[1])
        if "alibaba.com" in mk:
            q = _re.search(r"SearchText=([^&]+)", url).group(1).replace("+", " ")
            base = 200 + (sum(map(ord, q)) % 50) * 3 + (page - 1) * 20
            out = []
            for k in range(base, base + min(n, 8)):
                title = "Wholesale OEM %s Supplier Lot %s" % (ADJS[k % len(ADJS)], ["Alpha", "Beta", "Gamma", "Delta", "Omega", "Sigma", "Zeta", "Kappa"][k % 8])
                out.append({"url": "https://www.alibaba.com/product-detail/fixture_%d.html" % k, "name": title + " - Buy  Product on Alibaba.com",
                            "offers": {"price": round(0.5 + (k % 30) / 3, 2), "priceCurrency": "USD", "availability": "InStock"},
                            "brand": {"name": "Fixture Supplier Co. %d" % (k % 9)}, "image": image_for(title),
                            "additionalProperties": {"sku": str(1600000000000 + k), "categories": ["Vehicle Accessories, Electronics & Tools",
                                                                                                     "Interior Accessories", ["Car Organizers", "Seat Covers", "Car Fragrance"][k % 3]]},
                            "marketplace": "www.alibaba.com"})
            return out
        cat = _cat_from(url)
        if "/bestsellers/" in url:
            start = (page - 1) * 20
            return [_amazon_row(mk, cat, k, "zg_bs", k + 1, url, with_date=(k % 3 == 0)) for k in range(start, start + min(n, 20))]
        if "/new-releases/" in url:
            start = 40 + (page - 1) * 20
            rows = [_amazon_row(mk, cat, k, "zg_bsnr", k - 39, url) for k in range(start, start + min(n, 20) - 2)]
            if page == 1:  # two current bestsellers also appear on New Releases -> one product, both badges
                rows += [_amazon_row(mk, cat, k, "zg_bsnr", 30 + k, url, with_date=False) for k in (0, 1)]
            return rows
        return []
    mk = (payload.get("marketplaces") or [""])[0]
    if "amazon." in mk:
        cat = "automotive"
        base = 100 + sum(map(ord, payload.get("keyword", ""))) % 40
        return [_amazon_row(mk, cat, k, None, 0, None) for k in range(base, base + min(n, 6))]
    return []  # specialist marketplaces return nothing in the fixture (exercises the fallback)
