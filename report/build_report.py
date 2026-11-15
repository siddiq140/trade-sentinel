#!/usr/bin/env python3
"""Build the Trade Sentinel report page from a snapshot.

    python3 report/build_report.py                         # snapshots/latest.json -> report/trade-sentinel.html
    python3 report/build_report.py snapshots/2026-11-13/<cycle>.json --out somewhere.html
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
src = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else os.path.join(HERE, "..", "snapshots", "latest.json")
out = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else os.path.join(HERE, "trade-sentinel.html")
data = json.load(open(src))
html = open(os.path.join(HERE, "template.html")).read()
# keep "</script>" in any text from closing the page's script early
payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
open(out, "w").write(html.replace("__DATA__", payload))
print("report: %s -> %s (%d KB)" % (src, out, os.path.getsize(out) // 1024))
