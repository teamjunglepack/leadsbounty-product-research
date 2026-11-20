"""Row normalization and product identity/deduplication."""
import hashlib
import json
import re

STOP = set("""a an and the for with of to in on by new hot sale best 1pc pcs piece pieces set
portable mini multifunction multifunctional household home premium quality original free shipping
de la le les pour et avec un une du des en
مع و من في على جديد اصلي أصلي""".split())

UNIT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(mah|ml|l|w|v|cm|mm|inch|in|kg|g|gb|tb|m|hz|l)\b", re.I)
PACK_RE = re.compile(r"(?:(\d+)\s*(?:pcs|pc|pack|pieces|piece|count|ct|x|قطع|حبة|حبات|pièces))|(?:(?:pack|set|lot)\s*(?:of)?\s*(\d+))", re.I)
ASIN_RE = re.compile(r"/(?:dp|gp/product)/([A-Z0-9]{10})")
PRICE_RE = re.compile(r"(\d[\d,]*\.?\d*)")
TOKEN_RE = re.compile(r"[0-9a-zA-Z؀-ۿÀ-ſ]+")
VARIANT_KEYS = ("color", "colour", "size", "variant", "style", "capacity", "model", "pattern", "material")
ID_KEYS = ("gtin", "gtin8", "gtin12", "gtin13", "gtin14", "ean", "upc", "isbn", "asin", "mpn")


def _first(*vals):
    for v in vals:
        if v not in (None, "", [], {}):
            return v
    return None


def parse_price(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = PRICE_RE.search(str(v).replace("٫", "."))
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


CURRENCY_SYMBOLS = [("US$", "USD"), ("$", "USD"), ("€", "EUR"), ("£", "GBP"), ("ر.س", "SAR"), ("SAR", "SAR"), ("AED", "AED"),
                    ("د.إ", "AED"), ("OMR", "OMR"), ("ر.ع", "OMR"), ("MAD", "MAD"), ("DH", "MAD"), ("د.م", "MAD"),
                    ("LBP", "LBP"), ("ل.ل", "LBP"), ("CN¥", "CNY"), ("¥", "CNY"), ("EGP", "EGP")]


def currency_from_text(v):
    if not isinstance(v, str):
        return None
    for sym, code in CURRENCY_SYMBOLS:
        if sym in v:
            return code
    return None


def _props(row):
    ap = row.get("additionalProperties")
    out = {}
    if isinstance(ap, dict):
        out.update({str(k).lower(): v for k, v in ap.items()})
    elif isinstance(ap, list):
        for it in ap:
            if isinstance(it, dict) and it.get("name"):
                out[str(it["name"]).lower()] = it.get("value")
    return out


def normalize_rows(row, source_id, is_local, observed_at):
    """Turn one actor dataset item into one or more seller offers."""
    if isinstance(row.get("product"), dict) and isinstance(row.get("sellers"), list):
        p = row["product"]
        base = {"name": p.get("title") or p.get("name"), "image": p.get("image") or p.get("thumbnail"),
                "description": p.get("description"), "url": p.get("url") or row.get("url")}
        offers = []
        for s in row["sellers"] or [{}]:
            r = dict(base)
            r["url"] = s.get("url") or base["url"]
            r["offers"] = {"price": s.get("price") or p.get("price"), "priceCurrency": s.get("currency")}
            r["seller"] = s.get("merchantName") or s.get("name")
            offers.append(normalize(r, source_id, is_local, observed_at))
        return offers
    return [normalize(row, source_id, is_local, observed_at)]


def normalize(row, source_id, is_local, observed_at):
    props = _props(row)
    offers = row.get("offers") if isinstance(row.get("offers"), dict) else {}
    if isinstance(row.get("offers"), list) and row["offers"]:
        offers = row["offers"][0] if isinstance(row["offers"][0], dict) else {}
    title = _first(row.get("name"), row.get("title"), props.get("title")) or ""
    image = row.get("image") or row.get("images") or row.get("thumbnail")
    if isinstance(image, list):
        image = image[0] if image else None
    if isinstance(image, dict):
        image = image.get("url")
    url = _first(row.get("url"), row.get("productUrl"), row.get("inputUrl"))
    brand = row.get("brand")
    if isinstance(brand, dict):
        brand = _first(brand.get("name"), brand.get("slogan"))
    seller = _first(row.get("seller") if isinstance(row.get("seller"), str) else None,
                    (row.get("seller") or {}).get("name") if isinstance(row.get("seller"), dict) else None,
                    offers.get("seller", {}).get("name") if isinstance(offers.get("seller"), dict) else None,
                    props.get("seller"), props.get("sold by"), row.get("marketplace"))
    ids = {}
    for k in ID_KEYS:
        v = _first(row.get(k), props.get(k))
        if v:
            ids[k] = str(v).strip()
    if url and "asin" not in ids:
        m = ASIN_RE.search(url)
        if m:
            ids["asin"] = m.group(1)
    variant = {k: str(props[k]) for k in VARIANT_KEYS if props.get(k)}
    features = row.get("features") or props.get("features") or props.get("about this item")
    if isinstance(features, str):
        features = [features]
    desc = _first(row.get("description"), row.get("aiSummary"))
    pack = _first(props.get("pack contents"), props.get("in the box"), props.get("package includes"),
                  props.get("what's in the box"))
    seller, brand = _text(seller), _text(brand)
    return {
        "source_id": source_id, "is_local": 1 if is_local else 0, "url": _text(url), "seller": seller,
        "title": title.strip(), "brand": brand, "model": _text(_first(props.get("model"), props.get("model number"), row.get("model"))),
        "variant": variant, "price": parse_price(_first(offers.get("price"), row.get("price"))),
        "currency": _first(offers.get("priceCurrency"), row.get("currency"),
                           currency_from_text(offers.get("price")), currency_from_text(row.get("price"))),
        "image": _text(image), "description": _text(desc), "features": features or [], "pack_contents": _text(pack),
        "identifiers": ids, "observed_at": _first(row.get("scrapedAt"), observed_at),
    }


def _text(v):
    """Marketplaces sometimes return objects where text is expected (e.g. seller: {name, url})."""
    if v is None or isinstance(v, str):
        return v
    if isinstance(v, dict):
        return _text(v.get("name") or v.get("title") or v.get("value")) or json.dumps(v, ensure_ascii=False)
    if isinstance(v, (list, tuple)):
        return ", ".join(t for t in (_text(x) for x in v) if t)
    return str(v)


def tokens(text):
    return {t.lower() for t in TOKEN_RE.findall(text or "") if t.lower() not in STOP and len(t) > 1 and not t.isdigit()}


def specs(text):
    return sorted({"%g%s" % (float(n), u.lower()) for n, u in UNIT_RE.findall(text or "")})


def pack_count(text):
    m = PACK_RE.search(text or "")
    if not m:
        return 1
    n = int(m.group(1) or m.group(2))
    return n if 0 < n < 1000 else 1


def identity_key(offer):
    ids = offer["identifiers"]
    for k in ("gtin13", "gtin", "ean", "upc", "gtin12", "gtin14", "gtin8", "isbn", "asin"):
        if ids.get(k):
            return "%s:%s" % ("gtin" if k.startswith("gtin") or k in ("ean", "upc") else k, ids[k])
    if offer.get("brand") and offer.get("model"):
        return "bm:%s|%s" % (str(offer["brand"]).lower().strip(), str(offer["model"]).lower().strip())
    return None


def fingerprint(offer):
    text = " ".join([offer["title"] or "", " ".join(offer["variant"].values()), str(offer.get("pack_contents") or "")])
    toks = sorted(tokens(text))
    sp = specs(text)
    pk = pack_count(text)
    return {"tokens": toks, "specs": sp, "pack": pk,
            "hash": hashlib.sha1(("|".join(toks) + "#" + ",".join(sp) + "#%d" % pk).encode()).hexdigest()[:16]}


DUP_THRESHOLD = 0.80
UNCERTAIN_THRESHOLD = 0.55


def similarity(fa, fb):
    """Score two fingerprints. Different specs or pack counts are different products."""
    if fa["specs"] and fb["specs"] and fa["specs"] != fb["specs"]:
        return 0.0, "different specs"
    if fa["pack"] != fb["pack"]:
        return 0.0, "different pack contents"
    a, b = set(fa["tokens"]), set(fb["tokens"])
    if not a or not b:
        return 0.0, "no tokens"
    return len(a & b) / len(a | b), "title/spec similarity"


class Matcher:
    """In-memory index of known products for one country."""

    def __init__(self):
        self.by_identity = {}
        self.by_hash = {}
        self.items = []  # (product_id, fp)
        self.token_index = {}

    def add(self, product_id, ident, fp):
        if ident:
            self.by_identity[ident] = product_id
        self.by_hash[fp["hash"]] = product_id
        idx = len(self.items)
        self.items.append((product_id, fp))
        for t in fp["tokens"]:
            self.token_index.setdefault(t, []).append(idx)

    def match(self, ident, fp):
        """Return (relation, product_id, score, reason). relation: same | uncertain | new."""
        if ident and ident in self.by_identity:
            return "same", self.by_identity[ident], 1.0, "identical identifier %s" % ident
        if fp["hash"] in self.by_hash:
            return "same", self.by_hash[fp["hash"]], 1.0, "identical title/spec/pack fingerprint"
        cand = {}
        for t in fp["tokens"]:
            for i in self.token_index.get(t, ()):
                cand[i] = cand.get(i, 0) + 1
        best = (0.0, None, "")
        for i, _ in sorted(cand.items(), key=lambda kv: -kv[1])[:50]:
            pid, ofp = self.items[i]
            s, why = similarity(fp, ofp)
            if s > best[0]:
                best = (s, pid, why)
        if best[0] >= DUP_THRESHOLD:
            return "same", best[1], best[0], best[2]
        if best[0] >= UNCERTAIN_THRESHOLD:
            return "uncertain", best[1], best[0], best[2]
        return "new", None, best[0], ""
