"""Build the bundle that goes into Composio's remote workbench.

    python3 -I scout/composio_bundle.py

Writes scout/lead/workbench-bundle.b64 (git-ignored): a base64 zip holding the pipeline modules, a tiny stand-in for
scout.py, the selected tracker rows, and the local suppression and spend files. Nothing secret is in it.
"""
import base64
import csv
import io
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SHIM = '''"""Stand-in for scout.py inside the workbench: only what lead.py uses."""
import csv
import os
from norm import street_key, zip5  # noqa: F401

HOME = os.environ.get("SCOUT_HOME", ".")
TRACKER = os.path.join(HOME, "scout-tracker.csv")


def read_csv(path):
    if not os.path.exists(path):
        return [], []
    with open(path, newline="", encoding="utf-8") as f:
        rd = csv.DictReader(f)
        return list(rd), rd.fieldnames or []


def write_csv(path, fields, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\\n", extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    os.replace(tmp, path)
'''


def build():
    home = os.environ.get("SCOUT_HOME") or ROOT
    rows = list(csv.DictReader(open(os.path.join(home, "scout-tracker.csv"), newline="", encoding="utf-8")))
    fields = list(rows[0].keys())
    sel = [r for r in rows if r["eligibility_status"] == "ready" and r["outreach_status"] == "ready"]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, lineterminator="\n")
    w.writeheader()
    w.writerows(sel)
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for n in ("lead.py", "norm.py", "composio_transport.py"):
            zf.write(os.path.join(HERE, n), "scout/" + n)
        zf.writestr("scout/scout.py", SHIM)
        zf.writestr("scout-tracker.csv", buf.getvalue())
        for n in ("suppression.csv", "spend.json"):
            p = os.path.join(home, "scout", "lead", n)
            if os.path.exists(p):
                zf.write(p, "scout/lead/" + n)
    b64 = base64.b64encode(z.getvalue()).decode()
    out = os.path.join(home, "scout", "lead", "workbench-bundle.b64")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, "w").write(b64)
    print(f"{out}: {len(b64):,} characters, {len(sel)} selected tracker row(s): {[r['biz_id'] for r in sel]}")
    return out


if __name__ == "__main__":
    build()
