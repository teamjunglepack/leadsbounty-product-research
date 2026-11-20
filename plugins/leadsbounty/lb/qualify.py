"""Qualification: independent demand + local signal + exact supplier + defensible price + COD economics.

Break-even ad cost per submitted lead (all USD per unit):
  landed        = supplier_price * landed_cost_multiplier + shipping_to_country_per_unit
  per_delivered = selling_price - landed - fulfillment_fee - delivery_fee
  per_failed    = delivery_fee + return_cost          (unit returns to stock)
  breakeven_cpl = confirmation_rate * (delivery_rate * per_delivered - (1 - delivery_rate) * per_failed)
Any missing input keeps the product PENDING.
"""
import statistics

ECON_KEYS = ("confirmation_rate", "delivery_rate", "delivery_fee_usd", "return_cost_usd",
             "fulfillment_fee_usd", "shipping_to_country_per_unit_usd")


def economics(selling_price, supplier_price, econ):
    missing = [k for k in ECON_KEYS if econ.get(k) is None]
    if selling_price is None:
        missing.append("selling_price")
    if supplier_price is None:
        missing.append("supplier_price")
    if missing:
        return {"complete": False, "missing": missing}
    landed = supplier_price * (econ.get("landed_cost_multiplier") or 1.0) + econ["shipping_to_country_per_unit_usd"]
    per_delivered = selling_price - landed - econ["fulfillment_fee_usd"] - econ["delivery_fee_usd"]
    per_failed = econ["delivery_fee_usd"] + econ["return_cost_usd"]
    d, c = econ["delivery_rate"], econ["confirmation_rate"]
    cpl = c * (d * per_delivered - (1 - d) * per_failed)
    return {"complete": True, "selling_price": round(selling_price, 2), "supplier_price": round(supplier_price, 2),
            "landed_cost": round(landed, 2), "profit_per_delivered_before_ads": round(per_delivered, 2),
            "loss_per_failed_delivery": round(per_failed, 2), "confirmation_rate": c, "delivery_rate": d,
            "breakeven_cpl": round(cpl, 2),
            "formula": "c*(d*(price-landed-fulfil-delivery)-(1-d)*(delivery+return))"}


def decide(product, country_cfg, cfg, local_offers, supplier, demand):
    """Return (status, reasons[], economics, biggest_uncertainty).

    local_offers: retail offers in the target country (is_local=1) with price_usd.
    supplier: {"status": ok|unavailable|not_found|not_run, "price_usd", "url", "detail"}
    demand: {"status": ok|unavailable, "summary": {...}, "source_url"}
    """
    reasons, pending, rejects = [], [], []
    min_price = country_cfg.get("min_selling_price_usd")
    dcfg = cfg["demand"]

    # 1. Independent demand
    if demand["status"] != "ok":
        pending.append("Demand evidence unavailable: %s" % demand["summary"].get("error", "provider not configured"))
    else:
        s = demand["summary"]
        if not s.get("points"):
            pending.append("Google Trends has no data for '%s' in %s (search volume too low to measure)" % (s["query"], s["geo"]))
        elif s["avg"] < dcfg["min_avg_interest"]:
            rejects.append("Low demand: Google Trends average interest %s (< %s) for '%s' in %s, %s to %s" % (
                s["avg"], dcfg["min_avg_interest"], s["query"], s["geo"], s["start"], s["end"]))
        else:
            reasons.append("Demand: Google Trends average interest %s for '%s' in %s (%s to %s, last %s)" % (
                s["avg"], s["query"], s["geo"], s["start"], s["end"], s["last"]))

    # 2. Local signal + defensible local price
    priced = [o for o in local_offers if o.get("price_usd")]
    local_price = None
    if not priced:
        pending.append("No priced local listing observed in %s" % country_cfg["name"])
    else:
        prices = sorted(o["price_usd"] for o in priced)
        local_price = statistics.median(prices)
        sellers = sorted({o.get("seller") or "unknown" for o in priced})
        reasons.append("Local signal: %d local offer(s) from %s, median USD %.2f (range %.2f-%.2f)" % (
            len(priced), ", ".join(sellers[:4]), local_price, prices[0], prices[-1]))
    selling_price = round(local_price * (cfg["economics"].get("price_markup_over_competitor") or 1.0), 2) if local_price else None
    if selling_price is not None:
        if min_price is None:
            pending.append("Minimum selling price for %s not configured" % country_cfg["name"])
        elif selling_price < min_price:
            rejects.append("Price too low: defensible local price USD %.2f below the USD %s minimum for %s" % (
                selling_price, min_price, country_cfg["name"]))
        else:
            reasons.append("Price: USD %.2f meets the USD %s minimum" % (selling_price, min_price))

    # 3. Exact supplier match
    if supplier["status"] == "ok":
        reasons.append("Supplier: exact variant match at USD %.2f (%s)" % (supplier["price_usd"], supplier["detail"]))
    elif supplier["status"] == "not_found":
        pending.append("No exact supplier match for this variant (%s)" % supplier.get("detail", ""))
    else:
        pending.append("Supplier search %s" % supplier["status"].replace("_", " "))

    # 4. COD economics
    eco = economics(selling_price, supplier.get("price_usd") if supplier["status"] == "ok" else None, cfg["economics"])
    if not eco["complete"]:
        miss = [m for m in eco["missing"] if m not in ("selling_price", "supplier_price")]
        if miss:
            pending.append("COD assumptions missing: %s" % ", ".join(miss))
    elif eco["breakeven_cpl"] <= 0:
        rejects.append("COD economics negative: break-even cost per lead USD %.2f" % eco["breakeven_cpl"])
    else:
        reasons.append("COD: break-even ad cost per submitted lead USD %.2f" % eco["breakeven_cpl"])

    if rejects:
        status, why = "REJECTED", rejects + pending
    elif pending:
        status, why = "PENDING", pending
    else:
        status, why = "QUALIFIED", reasons
    return status, why if status != "QUALIFIED" else reasons, eco, biggest_uncertainty(priced, demand, supplier, eco)


def biggest_uncertainty(priced, demand, supplier, eco):
    if demand["status"] != "ok":
        return "No independent demand data"
    s = demand["summary"]
    if s.get("points") and s.get("second_half_avg") is not None and s["second_half_avg"] < 0.7 * max(1, s["first_half_avg"]):
        return "Search interest is falling (%.0f -> %.0f)" % (s["first_half_avg"], s["second_half_avg"])
    if len(priced) <= 1:
        return "Only one local price point observed"
    if eco.get("complete") and eco["breakeven_cpl"] < 3:
        return "Thin margin: break-even CPL only USD %.2f" % eco["breakeven_cpl"]
    if supplier.get("score") is not None and supplier["score"] < 0.6:
        return "Supplier listing match is partial (overlap %.2f)" % supplier["score"]
    return "Delivery and confirmation rates are assumptions, not measured for this product"
