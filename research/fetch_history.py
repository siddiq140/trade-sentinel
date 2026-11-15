#!/usr/bin/env python3
"""Download long daily history for every symbol in trader-247/universe.json (plus the Nifty 50).

    python3 research/fetch_history.py [--range 5y] [--out trader-247/history]

Compact JSON per symbol: [[date, open, high, low, close, volume], ...]. Free Yahoo chart data,
6 requests at a time. Not committed to git (trader-247/ is ignored).
"""
import argparse, json, os, sys, time
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "skills", "trade-sentinel"))
import engine  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--range", default="5y")
ap.add_argument("--out", default="trader-247/history")
ap.add_argument("--universe", default="trader-247/universe.json")
a = ap.parse_args()
os.makedirs(a.out, exist_ok=True)
syms = [u["symbol"] for u in json.load(open(a.universe))] + ["^NSEI"]


def one(s):
    for attempt in range(3):
        try:
            bars, _ = engine.yahoo_bars(s, a.range)
            rows = [[b["date"], b["open"], b["high"], b["low"], b["close"], b["volume"]] for b in bars]
            with open(os.path.join(a.out, engine.safe(s) + ".json"), "w") as f:
                json.dump(rows, f, separators=(",", ":"))
            return s, len(rows), None
        except Exception as ex:
            err = str(ex)
            time.sleep(2 * (attempt + 1))
    return s, 0, err


t0, ok, bad = time.time(), 0, []
with ThreadPoolExecutor(6) as ex:
    for i, (s, n, err) in enumerate(ex.map(one, syms), 1):
        if err:
            bad.append((s, err))
        else:
            ok += 1
        if i % 300 == 0:
            print("  %d/%d (%.0fs)" % (i, len(syms), time.time() - t0), flush=True)
print("history: %d/%d symbols in %.0fs -> %s" % (ok, len(syms), time.time() - t0, a.out))
for s, e in bad[:10]:
    print("  ! %s %s" % (s, e))
