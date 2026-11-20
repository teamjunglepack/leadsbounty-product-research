"""Dependency-free fallback PDF writer (text only, Latin-1). Used only when no Chrome/Edge is installed."""
from . import brand


def report_lines(job, counts, prods):
    lines = [brand.PRODUCT_NAME, "Job %s  country %s  target %d" % (job["id"], job["country"], job["target"])]
    if job["mode"] == "fixture":
        lines.append("FIXTURE DEMO - synthetic data, not real market evidence")
    lines.append("Status: %s  stop reason: %s" % (job["status"], job["stop_reason"]))
    lines.append("")
    for k, v in counts.items():
        lines.append("  %-22s %s" % (k, v))
    lines.append("")
    for p in prods:
        st = "UNCERTAIN" if p["relation"] == "uncertain" else (p["status"] or "PENDING")
        lines.append("[%s] %s" % (st, p["title"]))
        lines.append("   link: %s" % (p["source_url"] or "-"))
        lines.append("   found: %s" % p["discovery_reason"])
        for r in p["reasons"][:4]:
            lines.append("   - %s" % r)
    lines += ["", brand.PUBLISHER + " - " + brand.WEBSITE,
              "Images are in report.html; this text PDF was produced because no Chrome/Edge was found."]
    return lines


def _esc(s):
    s = s.encode("latin-1", "replace").decode("latin-1")
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def write_text_pdf(path, lines, per_page=58):
    wrapped = []
    for ln in lines:
        while len(ln) > 110:
            wrapped.append(ln[:110])
            ln = "     " + ln[110:]
        wrapped.append(ln)
    pages = [wrapped[i:i + per_page] for i in range(0, max(1, len(wrapped)), per_page)] or [[""]]
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", None, "<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>"]
    kids = []
    for pg in pages:
        text = "BT /F1 8 Tf 30 570 Td 10 TL " + " ".join("(%s) '" % _esc(l) for l in pg) + " ET"
        objs.append("<< /Length %d >>\nstream\n%s\nendstream" % (len(text.encode("latin-1")), text))
        content_id = len(objs)
        objs.append("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 842 595] /Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>" % content_id)
        kids.append(len(objs))
    objs[1] = "<< /Type /Pages /Kids [%s] /Count %d >>" % (" ".join("%d 0 R" % k for k in kids), len(kids))
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += ("%d 0 obj\n%s\nendobj\n" % (i, o)).encode("latin-1")
    xref = len(out)
    out += ("xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)).encode()
    for off in offsets:
        out += ("%010d 00000 n \n" % off).encode()
    out += ("trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)).encode()
    with open(path, "wb") as f:
        f.write(out)
