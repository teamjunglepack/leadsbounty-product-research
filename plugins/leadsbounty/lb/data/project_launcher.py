"""Leads Bounty project launcher (written by /leadsbounty:setup). Finds the installed plugin and runs its CLI.
Both /leadsbounty and /leadsbounty:<cmd> end up in the same lb.cli.main and the same worker."""
import glob
import json
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def plugin_root():
    rt = os.path.join(HERE, "runtime.json")
    if os.path.exists(rt):
        with open(rt, encoding="utf-8") as f:
            root = json.load(f).get("plugin_root")
        if root and os.path.exists(os.path.join(root, "scripts", "lb.py")):
            return root
    home = os.path.join(os.path.expanduser("~"), ".claude", "plugins")
    found = glob.glob(os.path.join(home, "**", "leadsbounty", "**", "scripts", "lb.py"), recursive=True)
    found = [f for f in found if os.path.exists(os.path.join(os.path.dirname(os.path.dirname(f)), "lb", "cli.py"))]
    if not found:
        sys.exit("Leads Bounty plugin not found. Install it: /plugin install leadsbounty@leadsbounty-community, then /leadsbounty:setup")
    return os.path.dirname(os.path.dirname(max(found, key=os.path.getmtime)))


if __name__ == "__main__":
    root = plugin_root()
    os.environ.setdefault("LEADSBOUNTY_PROJECT", os.path.dirname(HERE))
    sys.argv = [os.path.join(root, "scripts", "lb.py")] + sys.argv[1:]
    runpy.run_path(sys.argv[0], run_name="__main__")
