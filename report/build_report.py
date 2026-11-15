#!/usr/bin/env python3
"""Build the Trade Sentinel report from a snapshot, and (with --images) pictures GitHub can show.

    python3 report/build_report.py                     # snapshots/latest.json -> report/trade-sentinel.html
    python3 report/build_report.py --images            # also PNGs + README.md in snapshots/<date>/ and snapshots/
    python3 report/build_report.py <snapshot.json> [--out page.html] [--images] [--snapshots-dir DIR]
    python3 report/build_report.py swing/snapshots/latest.json --out swing/report/trade-sentinel-swing.html \
        --snapshots-dir swing/snapshots --images          # the 1-10 day swing run

GitHub shows code, not pages, for .html and .json files. The images and README.md files are
what you see when you open the snapshots/ folder on github.com. --images needs Node with
Playwright (preinstalled in Claude cloud sessions).
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
args = [a for a in sys.argv[1:]]
images = "--images" in args
out = args[args.index("--out") + 1] if "--out" in args else os.path.join(HERE, "trade-sentinel.html")
snap_dir = args[args.index("--snapshots-dir") + 1] if "--snapshots-dir" in args else os.path.join(ROOT, "snapshots")
pos = [a for i, a in enumerate(args) if not a.startswith("--") and (i == 0 or args[i - 1] not in ("--out", "--snapshots-dir"))]
src = pos[0] if pos else os.path.join(ROOT, "snapshots", "latest.json")

data = json.load(open(src))
SWING = data.get("style") == "swing"
NAME = "Trade Sentinel Swing" if SWING else "Trade Sentinel"
html = open(os.path.join(HERE, "template.html")).read()
if SWING:
    html = html.replace("<title>Trade Sentinel · NSE Cycle</title>", "<title>Trade Sentinel · NSE Swing</title>", 1)
os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")  # keep "</script>" in text from closing the script
open(out, "w").write(html.replace("__DATA__", payload))
print("report: %s -> %s (%d KB)" % (src, out, os.path.getsize(out) // 1024))


def fmt(n, d=2):
    if n is None:
        return "–"
    s = "{:,.{d}f}".format(float(n), d=d)
    return s


def sh(sym):
    return str(sym or "").replace(".NS", "")


def readme(data, img_prefix, title):
    b, f = data["breadth"], data["funnel"]
    sig = {s["symbol"]: s for s in data["signals"]}
    risk = {r["symbol"]: r for r in data["risk"]}
    lines = [
        "# %s" % title, "",
        "NSE data to **%s** · cycle `%s` · Nifty 50 %s (%s%%)" % (b["date"], data["cycle_id"], fmt(data["index"]["close"]),
                                                               fmt(data["index"]["chg"])), "",
        "**%s stocks checked** · %s advanced, %s declined · %s%% above their 50-day average · "
        "%s near a 20-day high, %s near a low" % (f"{b['stocks']:,}", b["advancers"], b["decliners"],
                                                  fmt(b["above_sma50_pct"], 0), b["near_20d_high"], b["near_20d_low"]), "",
        "%s NSE equities → %s liquid → %s shortlisted → %s scanned → %s signals passed → %s planned → "
        "**%s approved**" % (f"{f['universe']:,}", f["liquid"], f["shortlist"], f["scan"], f["signal_pass"],
                             f["planned"], f["approved"]), "",
    ]
    if data.get("swing"):
        sw = data["swing"]
        lines += ["**Swing rules:** enter within %s sessions, near today's price (within %s ATR); target at most %s ATR away; "
                  "exit by the close of day %s after entry if neither stop nor target hits. All 12 risk rules still apply." % (
                      sw["max_wait_bars"], fmt(sw["max_entry_gap_atr"], 1), fmt(sw["max_target_atr"], 1), sw["max_hold_bars"]), ""]
    lines += ["## Waiting for your answer", ""]
    if data["cards"]:
        lines += ["| Stock | Setup | Score | Entry zone | Stop | Target | R:R | Size | Risk |",
                  "| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |"]
        for c in data["cards"]:
            p = next(x for x in data["plans"] if x["symbol"] == c["symbol"])
            r, s = risk.get(c["symbol"], {}), sig.get(c["symbol"], {})
            lines.append("| **%s** | %s | %s | %s – %s | %s | %s | %s | %s | ₹%s |" % (
                sh(c["symbol"]), s.get("setup"), s.get("score"), fmt(p["entry_zone"][0]), fmt(p["entry_zone"][1]),
                fmt(p["stop"]), fmt(p["target"]), p["rr"], r.get("size"), fmt(r.get("risk_amount"))))
    else:
        lines.append("No plan passed every gate in this cycle.")
    blocked = [r for r in data["risk"] if r["verdict"] == "BLOCKED"]
    if blocked:
        def why(r):
            out = []
            for g in r["gates"]:
                if g["pass"]:
                    continue
                if g["id"] == "G2_EXPOSURE":
                    out.append("needs %s shares, %s%% of the account (limit %s%%)" % (r["size"], fmt(g["value"], 0), g["limit"]))
                else:
                    out.append("%s %s vs %s" % (g["id"], g["value"], g["limit"]))
            return ", ".join(out)
        lines += ["", "**Blocked:** " + "; ".join("%s %s" % (sh(r["symbol"]), why(r)) for r in blocked) + "."]
    lines += ["", "![Decision cards](%scards.png)" % img_prefix, "",
              "## Market and funnel", "", "![Market summary](%ssummary.png)" % img_prefix, "",
              "## Every plan", "", "![Every plan](%splans.png)" % img_prefix, "",
              "## Signal scores", "", "![Signal scores](%sscores.png)" % img_prefix, "",
              "Whole page as one image: [full.png](%sfull.png). Data: [%s](%s%s)." % (
                  img_prefix, os.path.basename(data_file), img_prefix, os.path.basename(data_file)), "",
              "_Educational, not investment advice. You place every order yourself._", ""]
    return "\n".join(lines)


if images:
    day = data["breadth"]["date"]
    day_dir = os.path.join(snap_dir, day)
    os.makedirs(day_dir, exist_ok=True)
    data_file = os.path.join(day_dir, (data["cycle_id"] or "cycle").replace(":", "") + ".json")
    r = subprocess.run(["node", os.path.join(HERE, "screenshot.js"), out, day_dir], capture_output=True, text=True)
    print(r.stdout.rstrip() or r.stderr.rstrip())
    if r.returncode != 0:
        sys.exit("images failed: %s" % r.stderr.strip())
    open(os.path.join(day_dir, "README.md"), "w").write(readme(data, "", "%s · %s" % (NAME, day)))
    open(os.path.join(snap_dir, "README.md"), "w").write(
        readme(data, day + "/", "%s · latest run (%s)" % (NAME, day)).replace(
            "\n## Waiting", "\nOlder runs: open a dated folder above.\n\n## Waiting", 1))
    print("readme: %s/README.md and %s/%s/README.md" % (snap_dir, snap_dir, day))
