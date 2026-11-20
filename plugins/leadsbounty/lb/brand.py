"""Single source of truth for Leads Bounty branding and report theme.

To use the official logo, place it at lb/data/brand/logo.png (or .svg). If no
logo file exists the text wordmark is used everywhere.
"""
import base64
import os

PRODUCT_NAME = "Leads Bounty Product Research"
PUBLISHER = "Published by Leads Bounty LLC"
WEBSITE = "leadsbounty.com"
WEBSITE_URL = "https://leadsbounty.com"
WORDMARK = "LEADS BOUNTY"

THEME = {
    "ink": "#13151a",
    "muted": "#5b6170",
    "paper": "#ffffff",
    "panel": "#f5f6f8",
    "line": "#dfe2e8",
    "accent": "#1b3a6b",       # navy blue
    "accent_dark_mode": "#7fa3e0",  # lighter navy for dark backgrounds
    "accent_ink": "#ffffff",
    "pass": "#1f8a4c",
    "fail": "#c92a2a",
    "pending": "#b7791f",
    "font": "-apple-system, 'Segoe UI', Roboto, 'Noto Sans Arabic', Arial, sans-serif",
}

_BRAND_DIR = os.path.join(os.path.dirname(__file__), "data", "brand")


def logo_data_uri():
    """Return a data URI for the official logo if supplied, else None."""
    for name, mime in (("logo.svg", "image/svg+xml"), ("logo.png", "image/png")):
        p = os.path.join(_BRAND_DIR, name)
        if os.path.exists(p):
            with open(p, "rb") as f:
                return "data:%s;base64,%s" % (mime, base64.b64encode(f.read()).decode())
    return None


def logo_html(height=28):
    uri = logo_data_uri()
    if uri:
        return '<img src="%s" alt="%s" style="height:%dpx">' % (uri, WORDMARK, height)
    return ('<span style="font-weight:800;letter-spacing:.12em;color:%s">LEADS'
            '<span style="color:%s"> BOUNTY</span></span>' % (THEME["ink"], THEME["accent"]))


def banner_text(subtitle=""):
    line = "=" * 64
    s = "%s\n  %s  |  %s\n" % (line, WORDMARK, PRODUCT_NAME)
    if subtitle:
        s += "  %s\n" % subtitle
    return s + line
