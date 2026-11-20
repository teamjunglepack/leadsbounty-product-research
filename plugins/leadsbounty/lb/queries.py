"""Keyword discovery planner for catalog searches.

1. Seeds: several distinct subcategories / buyer use cases per category, in the storefront's languages (Arabic + English for
   Gulf/Levant markets, French + Arabic for Morocco), plus supplier-style phrasing for Alibaba.
2. Expansion: subcategories and title phrases actually returned by the scraper, kept only when the marketplace category path
   belongs to the requested category (prevents drift such as "health" -> "commercial robots").
3. Stats per query: new distinct products, duplicate rate, cost per new product (when the provider reported cost).
4. Choice of the next batch: round-robin across subcategories (variety), skip equivalent queries already run for the same
   source and settings in any job, deprioritise subcategories whose recent queries stopped yielding new products.
"""
import json
import re

from . import dedupe

LANGS = {"SA": ["ar", "en"], "AE": ["ar", "en"], "OM": ["ar", "en"], "LB": ["ar", "en"], "EG": ["ar", "en"],
         "MA": ["fr", "ar"], "WW": ["en"]}

BANK = {
    "health": [
        {"id": "foot-massager", "en": "foot massager", "ar": "جهاز مساج للقدمين", "fr": "masseur pieds", "supplier": "electric shiatsu foot massager machine with heat"},
        {"id": "pill-organizer", "en": "pill organizer", "ar": "علبة تنظيم الحبوب", "fr": "pilulier semainier", "supplier": "7 day weekly pill box organizer AM PM"},
        {"id": "wrist-brace", "en": "adjustable wrist brace", "ar": "دعامة رسغ قابلة للتعديل", "fr": "attelle poignet", "supplier": "adjustable wrist support brace carpal tunnel splint"},
        {"id": "knee-support", "en": "knee support brace", "ar": "دعامة ركبة", "fr": "genouillère", "supplier": "compression knee sleeve brace with side stabilizers"},
        {"id": "posture", "en": "posture corrector", "ar": "مشد تصحيح وضعية الظهر", "fr": "correcteur de posture", "supplier": "adjustable back posture corrector belt"},
        {"id": "bp-monitor", "en": "blood pressure monitor", "ar": "جهاز قياس ضغط الدم", "fr": "tensiomètre", "supplier": "digital arm blood pressure monitor OEM"},
        {"id": "heating-pad", "en": "electric heating pad", "ar": "وسادة تدفئة كهربائية", "fr": "coussin chauffant", "supplier": "electric heating pad for back pain relief"},
        {"id": "oximeter", "en": "pulse oximeter", "ar": "جهاز قياس الأكسجين", "fr": "oxymètre de pouls", "supplier": "fingertip pulse oximeter OLED display"},
        {"id": "neck-massager", "en": "neck massager", "ar": "جهاز مساج الرقبة", "fr": "masseur cervical", "supplier": "smart EMS neck massager"},
        {"id": "thermometer", "en": "digital thermometer", "ar": "ميزان حرارة رقمي", "fr": "thermomètre digital", "supplier": "infrared forehead thermometer non contact"},
    ],
    "automotive": [
        {"id": "phone-holder", "en": "car phone holder", "ar": "حامل جوال للسيارة", "fr": "support téléphone voiture", "supplier": "magnetic car phone mount vent clip"},
        {"id": "tire-inflator", "en": "portable tire inflator", "ar": "منفاخ إطارات محمول", "fr": "gonfleur pneu portable", "supplier": "cordless digital tire inflator air compressor"},
        {"id": "dash-cam", "en": "dash cam", "ar": "كاميرا داش للسيارة", "fr": "caméra embarquée", "supplier": "1080P dual lens dash cam WiFi"},
        {"id": "car-vacuum", "en": "car vacuum cleaner", "ar": "مكنسة سيارة", "fr": "aspirateur voiture", "supplier": "cordless handheld car vacuum high suction"},
        {"id": "seat-organizer", "en": "car seat organizer", "ar": "منظم مقعد السيارة", "fr": "organisateur siège voiture", "supplier": "car back seat organizer with tablet holder"},
        {"id": "sun-shade", "en": "windshield sun shade", "ar": "مظلة زجاج السيارة", "fr": "pare-soleil voiture", "supplier": "foldable umbrella windshield sun shade"},
        {"id": "jump-starter", "en": "car jump starter", "ar": "جهاز تشغيل السيارة", "fr": "booster batterie", "supplier": "portable lithium car jump starter power bank"},
        {"id": "air-freshener", "en": "car air freshener", "ar": "معطر سيارة", "fr": "désodorisant voiture", "supplier": "car aroma diffuser vent clip fragrance"},
    ],
    "kitchen": [
        {"id": "chopper", "en": "vegetable chopper", "ar": "قطاعة خضار", "fr": "hachoir légumes", "supplier": "multifunction manual vegetable chopper slicer"},
        {"id": "frother", "en": "milk frother", "ar": "خفاقة حليب", "fr": "mousseur à lait", "supplier": "rechargeable handheld milk frother"},
        {"id": "oil-sprayer", "en": "oil sprayer", "ar": "بخاخ زيت", "fr": "pulvérisateur huile", "supplier": "glass olive oil sprayer bottle"},
        {"id": "food-storage", "en": "food storage containers", "ar": "علب حفظ الطعام", "fr": "boîtes conservation", "supplier": "airtight food storage container set"},
        {"id": "knife-sharpener", "en": "knife sharpener", "ar": "مسن سكاكين", "fr": "aiguiseur couteaux", "supplier": "4 stage kitchen knife sharpener"},
    ],
}
GENERIC = lambda cat: [{"id": cat, "en": cat, "ar": cat, "fr": cat, "supplier": "%s wholesale" % cat}]  # noqa: E731

ROOT_WORDS = {
    "health": ["health", "medical", "personal care", "wellness", "massage", "beauty", "rehabilitation", "physical therapy", "صحة"],
    "automotive": ["vehicle", "automotive", "auto", "car", "motorcycle", "سيارات"],
    "kitchen": ["kitchen", "home & garden", "household", "dining", "cookware", "home & kitchen"],
    "beauty": ["beauty", "personal care", "makeup", "skin", "hair"],
    "electronics": ["electronics", "consumer electronics", "computer", "phone", "audio"],
    "home": ["home", "garden", "furniture", "household"],
    "baby": ["baby", "mother", "kids", "infant"],
    "toys": ["toys", "games", "hobbies"],
    "sports": ["sports", "fitness", "outdoor", "exercise"],
    "pet": ["pet", "animal"],
}


def equivalent_key(marketplace, list_type, text, n):
    toks = sorted(dedupe.tokens(text or "")) or [(text or "").strip().lower()]
    return "%s|%s|%s|%s" % (marketplace, list_type, " ".join(toks), n)


def relevant(category, path):
    """Keep an expansion only if the marketplace category path belongs to the requested category."""
    words = ROOT_WORDS.get(category)
    if not words:
        return True
    p = (path or "").lower()
    return any(w in p for w in words)


def seeds(category, target, fronts, sub=None):
    bank = BANK.get(category) or GENERIC(category)
    if sub:
        bank = [{"id": sub, "en": sub, "ar": sub, "fr": sub, "supplier": sub}] + bank
    langs = LANGS.get(target, ["en"])
    out = []
    for s in bank:
        for mk, region, rel in fronts:
            for lang in langs:
                out.append({"id": "amz-q-%s-%s-%s" % (mk, s["id"], lang), "source": "amazon", "kind": "keyword", "marketplace": mk,
                            "region": region, "relation": rel, "list_type": "category_search", "keyword": s[lang], "lang": lang,
                            "category": category, "subcat": s["id"], "page": 1, "origin": "seed"})
        out.append({"id": "ali-q-%s" % s["id"], "source": "alibaba", "kind": "keyword", "marketplace": "www.alibaba.com",
                    "region": "GLOBAL-B2B", "relation": "global wholesale marketplace (supplier offers, not retail)",
                    "list_type": "category_search", "keyword": s["supplier"], "lang": "en", "category": category,
                    "subcat": s["id"], "page": 1, "origin": "seed"})
    return out


def expansions(con, job_id, category, fronts, limit=12):
    """Subcategories actually returned by the scraper (Alibaba leaf categories, Amazon sub-categories), on-category only."""
    found = {}
    for (payload,) in con.execute("SELECT payload FROM raw_rows WHERE job_id=?", (job_id,)):
        d = json.loads(payload)
        props = d.get("additionalProperties") if isinstance(d.get("additionalProperties"), dict) else {}
        path = " > ".join(props.get("categories") or []) or props.get("breadCrumbs") or ""
        leaf = (props.get("categories") or [None])[-1] or props.get("subCategory")
        if not leaf or not relevant(category, path):
            continue
        leaf = re.sub(r"^other\s+", "", leaf.strip(), flags=re.I).lower()
        found[leaf] = found.get(leaf, 0) + 1
    out = []
    for leaf, n in sorted(found.items(), key=lambda kv: -kv[1])[:limit]:
        for mk, region, rel in fronts:
            out.append({"id": "amz-x-%s-%s" % (mk, leaf), "source": "amazon", "kind": "keyword", "marketplace": mk, "region": region,
                        "relation": rel, "list_type": "category_search", "keyword": leaf, "lang": "en", "category": category,
                        "subcat": "x:" + leaf, "page": 1, "origin": "expansion (seen %d× in results)" % n})
        out.append({"id": "ali-x-%s" % leaf, "source": "alibaba", "kind": "keyword", "marketplace": "www.alibaba.com", "region": "GLOBAL-B2B",
                    "relation": "global wholesale marketplace (supplier offers, not retail)", "list_type": "category_search",
                    "keyword": leaf, "lang": "en", "category": category, "subcat": "x:" + leaf, "page": 1,
                    "origin": "expansion (seen %d× in results)" % n})
    return out


def query_stats(con, job_id, task_by_batch):
    """Per query: rows, new distinct products, duplicate rate and cost per new product (confirmed cost only)."""
    stats = {}
    for b in con.execute("SELECT id, ingest_offset, new_products, actual_cost, status FROM batches WHERE job_id=?", (job_id,)):
        t = task_by_batch.get(b["id"]) or {}
        if b["status"] not in ("ingested", "failed"):
            continue
        rows, new = b["ingest_offset"] or 0, b["new_products"] or 0
        stats[t.get("id", b["id"])] = {"query": t.get("keyword") or t.get("url"), "subcat": t.get("subcat"), "marketplace": t.get("marketplace"),
                                       "rows": rows, "new": new, "dup_rate": round(1 - new / rows, 3) if rows else None,
                                       "cost_per_new": round(b["actual_cost"] / new, 4) if (b["actual_cost"] is not None and new) else None}
    return stats


def already_run_keys(con, country, mode):
    keys = set()
    for b in con.execute("""SELECT b.input_json FROM batches b JOIN jobs j ON j.id=b.job_id WHERE j.country=? AND j.mode=?
                            AND b.status NOT IN ('planned','failed')""", (country, mode)):
        inp = json.loads(b["input_json"])
        if inp.get("keyword"):
            for mk in inp.get("marketplaces") or []:
                keys.add(equivalent_key(mk, "category_search", inp["keyword"], inp.get("maxProductResults")))
    return keys


def order(tasks, stats, done_ids, ran_keys, n, low_yield=0.15):
    """Round-robin across subcategories; skip equivalent queries; push tired subcategories to the back."""
    by_sub = {}
    for st in stats.values():
        if st["subcat"] and st["rows"]:
            by_sub.setdefault(st["subcat"], []).append(st["new"] / st["rows"])
    tired = {s for s, ys in by_sub.items() if len(ys) >= 2 and all(y < low_yield for y in ys[-2:])}
    seen_keys, groups = set(), {}
    for t in tasks:
        if t["id"] in done_ids:
            continue
        k = equivalent_key(t["marketplace"], t["list_type"], t.get("keyword"), n)
        if k in ran_keys or k in seen_keys:
            continue
        seen_keys.add(k)
        groups.setdefault(t.get("subcat"), []).append(t)
    fresh = [s for s in groups if s not in tired]
    out = []
    while any(groups[s] for s in fresh):
        for s in fresh:
            if groups[s]:
                out.append(groups[s].pop(0))
    for s in groups:
        if s in tired:
            out.extend(groups[s])
    return out, sorted(tired)
