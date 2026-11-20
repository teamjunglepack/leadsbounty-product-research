"""Shortlist verification: image loads, supplier link matches the exact variant."""
import socket
import urllib.error
import urllib.request

from . import dedupe

IMAGE_MAGIC = (b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"RIFF", b"<svg", b"<?xm")


def check_image(url, timeout=20):
    """Return (ok, detail). Downloads at most 64 KB and checks content type and magic bytes."""
    if not url or not str(url).startswith(("http://", "https://")):
        return False, "no image URL"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 leadsbounty-image-check"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ctype = r.headers.get("Content-Type", "")
            head = r.read(65536)
    except urllib.error.HTTPError as e:
        return False, "HTTP %s" % e.code
    except (urllib.error.URLError, socket.timeout, ValueError, ConnectionError) as e:
        return False, "unreachable: %s" % getattr(e, "reason", e)
    if not head:
        return False, "empty response"
    if ctype.startswith("image/") or head[:4] in IMAGE_MAGIC or head[:3] in IMAGE_MAGIC:
        return True, "loads (%s, %d+ bytes)" % (ctype or "image", len(head))
    return False, "not an image (%s)" % (ctype or "unknown type")


def supplier_variant_match(product, supplier_offer):
    """Exact supplier match requires same specs, same pack count and strong title overlap."""
    pf = {"tokens": dedupe.tokens(product["title"]), "specs": dedupe.specs(product["title"] + " " + (product.get("variant") or "")),
          "pack": product.get("pack_count") or 1}
    stext = " ".join([supplier_offer.get("title") or "", supplier_offer.get("variant") or ""])
    sf = {"tokens": dedupe.tokens(stext), "specs": dedupe.specs(stext), "pack": dedupe.pack_count(stext)}
    if pf["specs"] and sf["specs"] and pf["specs"] != sf["specs"]:
        return False, 0.0, "specs differ: %s vs %s" % (pf["specs"], sf["specs"])
    if pf["specs"] and not sf["specs"]:
        return False, 0.0, "supplier listing does not state specs %s" % pf["specs"]
    if pf["pack"] != sf["pack"]:
        return False, 0.0, "pack contents differ: %s vs %s" % (pf["pack"], sf["pack"])
    a, b = set(pf["tokens"]), set(sf["tokens"])
    score = len(a & b) / len(a | b) if a and b else 0.0
    if score >= 0.45:
        return True, round(score, 2), "title overlap %.2f, specs %s, pack %s match" % (score, pf["specs"] or "n/a", pf["pack"])
    return False, round(score, 2), "title overlap only %.2f" % score
