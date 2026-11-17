#!/usr/bin/env python3
"""Follow every approved decision card and say what the price did with it (paper results).

    python3 report/track.py                       # main runs: snapshots/  -> snapshots/TRACKER.md
    python3 report/track.py --snapshots-dir swing/snapshots
    python3 report/track.py --bars trader-247/bars --json

For each card, day by day after the signal date, using daily bars:
  WAITING      price has not traded inside the entry zone yet (and the wait has not run out)
  EXPIRED      no entry within the wait (time_stop_bars sessions)
  CANCELLED    a daily close below the invalidation level before entry
  OPEN         entered; neither stop nor target hit yet (hold limit not reached)
  TARGET       target hit after entry           result = +R
  STOP         stop hit after entry             result = -1R (worse if it opened below the stop)
  TIME EXIT    swing hold limit reached: exit at that day's close
Entry price: the zone top if the day traded down into the zone, or the open if it opened inside it.
If stop and target are both inside one day's range, the stop is assumed first. Costs are not included.
A card is followed whether or not you took it: this is how the setup did, not your P&L.
"""
import argparse
import datetime as dt
import glob
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ap = argparse.ArgumentParser()
ap.add_argument("--snapshots-dir", default=os.path.join(ROOT, "snapshots"))
ap.add_argument("--bars", default=os.path.join(ROOT, "trader-247", "bars"))
ap.add_argument("--json", action="store_true", help="print the rows as JSON instead of writing TRACKER.md")
a = ap.parse_args()


def safe(sym):
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in sym)


def invalid_level(p):
    m = re.search(r"below\s+([0-9][0-9,]*\.?[0-9]*)", p.get("invalidation", ""))
    return float(m.group(1).replace(",", "")) if m else p["entry_zone"][0]


def follow(p, bars, signal_day):
    z0, z1, stop, tgt = p["entry_zone"][0], p["entry_zone"][1], p["stop"], p["target"]
    wait, hold = p.get("time_stop_bars", 10), p.get("max_hold_bars")
    inv = invalid_level(p)
    after = [b for b in bars if b["date"] > signal_day]
    last = bars[-1] if bars else None
    entry = None
    for i, b in enumerate(after):
        if entry is None:
            if i >= wait:
                return {"status": "EXPIRED", "detail": "no entry in %d sessions" % wait, "days": i}
            if b["open"] <= stop:
                return {"status": "CANCELLED", "detail": "%s opened %.2f, below the stop" % (b["date"], b["open"]), "days": i + 1}
            if b["low"] <= z1 and b["high"] >= z0:
                px = b["open"] if z0 <= b["open"] <= z1 else z1
                entry = {"date": b["date"], "price": round(px, 2), "i": i}
            elif b["close"] < inv:
                return {"status": "CANCELLED", "detail": "%s closed %.2f, below %.2f" % (b["date"], b["close"], inv), "days": i + 1}
            if entry is None:
                continue
        # in the trade (including the entry day)
        R = entry["price"] - stop
        later = i > entry["i"]
        if later and b["open"] <= stop:
            return dict(status="STOP", entry=entry, exit=b["open"], exit_date=b["date"], r=round((b["open"] - entry["price"]) / R, 2))
        if b["low"] <= stop:
            return dict(status="STOP", entry=entry, exit=stop, exit_date=b["date"], r=round((stop - entry["price"]) / R, 2))
        if b["high"] >= tgt:
            px = max(b["open"], tgt) if later else tgt
            return dict(status="TARGET", entry=entry, exit=px, exit_date=b["date"], r=round((px - entry["price"]) / R, 2))
        if hold and i - entry["i"] + 1 >= hold:
            return dict(status="TIME EXIT", entry=entry, exit=b["close"], exit_date=b["date"], r=round((b["close"] - entry["price"]) / R, 2))
    if entry is None:
        return {"status": "WAITING", "detail": "%d of %d sessions used" % (len(after), wait), "days": len(after), "last": last}
    R = entry["price"] - stop
    return {"status": "OPEN", "entry": entry, "last": last, "r_open": round((last["close"] - entry["price"]) / R, 2)}


rows = []
for f in sorted(glob.glob(os.path.join(a.snapshots_dir, "*", "*.json"))):
    snap = json.load(open(f))
    day = snap["breadth"]["date"]
    approved = {r["symbol"]: r for r in snap.get("risk", []) if r["verdict"] == "APPROVED"}
    for p in snap.get("plans", []):
        if p.get("status") != "PLANNED" or p["symbol"] not in approved:
            continue
        path = os.path.join(a.bars, safe(p["symbol"]) + ".json")
        bars = json.load(open(path)) if os.path.exists(path) else []
        res = follow(p, bars, day)
        r = approved[p["symbol"]]
        rows.append({"signal": day, "cycle": snap["cycle_id"], "symbol": p["symbol"].replace(".NS", ""), "zone": p["entry_zone"],
                     "stop": p["stop"], "target": p["target"], "size": r["size"], "risk": r["risk_amount"],
                     "last_close": bars[-1]["close"] if bars else None, "last_date": bars[-1]["date"] if bars else None, **res})

if a.json:
    print(json.dumps(rows, indent=1, default=str))
    raise SystemExit

f2 = lambda x: "{:,.2f}".format(x) if isinstance(x, (int, float)) else "–"
done = [x for x in rows if "r" in x]
title = "Swing card tracker" if "swing" in a.snapshots_dir else "Card tracker"
L = ["# %s" % title, "",
     "Every approved decision card, followed day by day on daily prices (updated %s). "
     "This is how each setup did on paper, whether or not you took it. Costs not included." % (
         max((x["last_date"] for x in rows if x["last_date"]), default="–")), ""]
if done:
    tot = sum(x["r"] for x in done)
    wins = sum(1 for x in done if x["r"] > 0)
    L += ["**Closed: %d** · won %d, lost %d · total %+.2fR · average %+.2fR a trade" % (
        len(done), wins, len(done) - wins, tot, tot / len(done)), ""]
L += ["| Signal | Stock | Entry zone | Stop | Target | Last close | Status | Detail |",
      "| --- | --- | --- | ---: | ---: | ---: | --- | --- |"]
for x in sorted(rows, key=lambda x: (x["signal"], x["symbol"]), reverse=True):
    st = x["status"]
    if st in ("TARGET", "STOP", "TIME EXIT"):
        det = "entered %s at %s, exit %s at %s: **%+.2fR**" % (x["entry"]["date"], f2(x["entry"]["price"]), x["exit_date"], f2(x["exit"]), x["r"])
    elif st == "OPEN":
        det = "entered %s at %s; now %+.2fR" % (x["entry"]["date"], f2(x["entry"]["price"]), x["r_open"])
    else:
        det = x.get("detail", "")
    icon = {"TARGET": "✅ Target", "STOP": "❌ Stop", "TIME EXIT": "⏱ Time exit", "OPEN": "🟢 Open", "WAITING": "⏳ Waiting",
            "EXPIRED": "⌛ Expired", "CANCELLED": "🚫 Cancelled"}.get(st, st)
    L.append("| %s | **%s** | %s – %s | %s | %s | %s | %s | %s |" % (
        x["signal"], x["symbol"], f2(x["zone"][0]), f2(x["zone"][1]), f2(x["stop"]), f2(x["target"]), f2(x["last_close"]), icon, det))
L += ["", "Rules: entry when the price trades inside the entry zone within the wait; cancelled on a close below the "
      "\"wrong if\" level or an open below the stop before entry; then stop, target or (swing) the hold limit. "
      "Stop first if both are hit the same day. R = result ÷ (entry − stop).", ""]
out = os.path.join(a.snapshots_dir, "TRACKER.md")
open(out, "w").write("\n".join(L))
print("tracker: %d cards (%d closed) -> %s" % (len(rows), len(done), out))
