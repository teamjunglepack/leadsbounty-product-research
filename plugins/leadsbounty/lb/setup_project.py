"""/leadsbounty:setup — safe to rerun. Installs the thin project skill and local folders. Never handles secrets."""
import json
import os

from . import __version__, brand
from .config import DATA_DIR, PLUGIN_ROOT, Paths, credential_status, credentials_file, save_json

MARKER = "<!-- leadsbounty-managed -->"
FALLBACK_SKILL = "leadsbounty-research"


def _skill_text(name):
    with open(os.path.join(DATA_DIR, "project_skill_template.md"), encoding="utf-8") as f:
        return f.read().replace("{{NAME}}", name).replace("{{VERSION}}", __version__).replace("{{MARKER}}", MARKER)


def _launcher_text():
    with open(os.path.join(DATA_DIR, "project_launcher.py"), encoding="utf-8") as f:
        return f.read()


def install_project_skill(project):
    """Return (installed_name, message)."""
    target_dir = os.path.join(project, ".claude", "skills", "leadsbounty")
    target = os.path.join(target_dir, "SKILL.md")
    if os.path.exists(target):
        with open(target, encoding="utf-8") as f:
            existing = f.read()
        if MARKER not in existing:
            alt_dir = os.path.join(project, ".claude", "skills", FALLBACK_SKILL)
            os.makedirs(alt_dir, exist_ok=True)
            with open(os.path.join(alt_dir, "SKILL.md"), "w", encoding="utf-8") as f:
                f.write(_skill_text(FALLBACK_SKILL))
            return FALLBACK_SKILL, (
                "CONFLICT: .claude/skills/leadsbounty/SKILL.md already exists and was not created by Leads Bounty. "
                "It was left untouched. Use /%s scrap (installed instead), or the native "
                "/leadsbounty:scrap. Rename or remove your existing skill and rerun setup to get /leadsbounty." % FALLBACK_SKILL)
    os.makedirs(target_dir, exist_ok=True)
    with open(target, "w", encoding="utf-8") as f:
        f.write(_skill_text("leadsbounty"))
    return "leadsbounty", "Installed project command /leadsbounty (.claude/skills/leadsbounty/SKILL.md)."


def ensure_gitignore(project):
    gi = os.path.join(project, ".gitignore")
    line = ".leadsbounty/"
    existing = open(gi, encoding="utf-8").read() if os.path.exists(gi) else ""
    if line not in existing.splitlines():
        with open(gi, "a", encoding="utf-8") as f:
            f.write(("" if existing.endswith("\n") or not existing else "\n") + "# Leads Bounty local research data (never commit)\n" + line + "\n")
        return True
    return False


def ensure_credentials_template():
    path = credentials_file()
    if os.path.exists(path):
        return path, False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(os.path.join(DATA_DIR, "credentials.example.env"), encoding="utf-8") as src, open(path, "w", encoding="utf-8") as dst:
        dst.write(src.read())
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path, True


def run(project):
    paths = Paths(project)
    paths.ensure()
    msgs = [brand.banner_text("Setup · v%s" % __version__)]
    if not os.path.exists(paths.config):
        save_json(paths.config, {"_note": "Your overrides. Defaults live in the plugin. Edit with: /leadsbounty config set <key> <value>",
                                 "budget": {"daily_spend_cap_usd": None}})
        msgs.append("Created .leadsbounty/config.json")
    else:
        msgs.append("Kept existing .leadsbounty/config.json")
    save_json(os.path.join(paths.home, "runtime.json"), {"plugin_root": PLUGIN_ROOT, "version": __version__})
    with open(os.path.join(paths.home, "lb.py"), "w", encoding="utf-8") as f:
        f.write(_launcher_text())
    name, m = install_project_skill(project)
    msgs.append(m)
    if ensure_gitignore(project):
        msgs.append("Added .leadsbounty/ to .gitignore")
    cpath, created = ensure_credentials_template()
    msgs.append(("Created" if created else "Found") + " your private credentials file: " + cpath)
    st = credential_status()
    msgs.append("Credentials: " + ", ".join("%s %s" % kv for kv in st.items()))
    msgs.append("Open that file in a text editor on your computer and paste your keys there. Never paste keys into Claude chat.")
    msgs.append("Next: /%s scrap   (you will be asked for market, period, category and target; nothing runs until you choose Start search)" % name)
    return name, "\n".join(msgs)
