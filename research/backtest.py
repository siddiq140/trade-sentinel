#!/usr/bin/env python3
"""5-year mechanical test of the swing setups on NSE daily bars.

    python3 research/backtest.py [--history trader-247/history] [--out research/results]

Rules are fixed in VARIANTS below before looking at results. Nov 2021 - Nov 2024 is the period the
rules are judged on ("in-sample"), Nov 2024 - Nov 2026 is held out ("out-of-sample"): no variant is
changed after seeing it. Every variant is reported, good or bad.

Common to all variants (the swing rules of trade-sentinel):
  - liquidity: average traded value over the prior 20 sessions >= Rs 10 crore
  - entry within 3 sessions of the signal; exit at the stop, the target, or the close of the 10th session
  - target = nearer of the prior swing high and entry_mid + 3 ATR; skip if reward:risk < 2
  - the setup's own close must sit within 1 ATR of the entry zone top
  - stop first when stop and target are both inside one day's range (conservative)
  - costs 0.3% of the entry price per round trip (delivery STT both sides, stamp, exchange, GST, DP)
Results are in R: profit or loss divided by the amount risked (entry - stop).

Known limits: only stocks listed today (survivorship), Yahoo split-adjusted prices, no slippage beyond
costs, the stop fills at the stop price unless the day opens below it, and Claude's chart judgment
(the SIGNAL score) is not part of the test.
"""
import argparse
import datetime as dt
import glob
import json
import math
import os
import statistics as st

ap = argparse.ArgumentParser()
ap.add_argument("--history", default="trader-247/history")
ap.add_argument("--out", default="research/results")
ap.add_argument("--split", default="2024-11-15", help="first date of the held-out period")
a = ap.parse_args()

COST = 0.003
D, O, H, L, C, V = range(6)

VARIANTS = [
    # name, setup, options
    ("B1", "Breakout as written: retest entry, stop 0.25 ATR under the level", "breakout",
     dict(stop_buf=0.25, trend=False, market=False, entry="retest")),
    ("B2", "Breakout, stop 1 ATR under the level, stock above a rising 50-day, Nifty above its 50-day", "breakout",
     dict(stop_buf=1.0, trend=True, market=True, entry="retest")),
    ("B3", "B2 but buy the next open after the breakout (no retest wait)", "breakout",
     dict(stop_buf=1.0, trend=True, market=True, entry="open")),
    ("P1", "Pullback to the 20-day average in an uptrend, higher low, limit entry in the zone", "pullback",
     dict(market=False, entry="retest")),
    ("P2", "P1 + Nifty above its 50-day", "pullback", dict(market=True, entry="retest")),
    ("P3", "P2 but buy the next open after the signal", "pullback", dict(market=True, entry="open")),
]


def load():
    stocks = {}
    for f in glob.glob(os.path.join(a.history, "*.NS.json")):
        rows = json.load(open(f))
        if len(rows) >= 120:
            stocks[os.path.basename(f)[:-8]] = rows
    idx = json.load(open(os.path.join(a.history, "_NSEI.json")))
    return stocks, idx


def prep(rows):
    """Per-bar indicators: ATR14 (Wilder), SMA20, SMA50, 20-day average volume and traded value (prior bars)."""
    n = len(rows)
    atr, sma20, sma50, vavg, tv = [None] * n, [None] * n, [None] * n, [None] * n, [None] * n
    a_ = None
    cs = [0.0]
    for r in rows:
        cs.append(cs[-1] + r[C])
    for i in range(1, n):
        tr = max(rows[i][H] - rows[i][L], abs(rows[i][H] - rows[i - 1][C]), abs(rows[i][L] - rows[i - 1][C]))
        if i == 14:
            a_ = sum(max(rows[k][H] - rows[k][L], abs(rows[k][H] - rows[k - 1][C]), abs(rows[k][L] - rows[k - 1][C]))
                     for k in range(1, 15)) / 14
        elif i > 14:
            a_ = (a_ * 13 + tr) / 14
        atr[i] = a_
        if i >= 19:
            sma20[i] = (cs[i + 1] - cs[i - 19]) / 20
        if i >= 49:
            sma50[i] = (cs[i + 1] - cs[i - 49]) / 50
        if i >= 20:
            prior = rows[i - 20:i]
            vavg[i] = sum(x[V] for x in prior) / 20
            tv[i] = sum(x[C] * x[V] for x in prior) / 20
    return atr, sma20, sma50, vavg, tv


def nifty_ok(idx):
    ok, cs = {}, [0.0]
    for r in idx:
        cs.append(cs[-1] + r[C])
    for i in range(49, len(idx)):
        ok[idx[i][D]] = idx[i][C] > (cs[i + 1] - cs[i - 49]) / 50
    return ok


def simulate(rows, t, z0, z1, stop, target, entry_mode):
    """-> (entry_price, exit_price, exit_kind, entry_index) or None if never filled / unfinished."""
    n = len(rows)
    ei = px = None
    if entry_mode == "open":
        if t + 1 >= n:
            return None
        o = rows[t + 1][O]
        if o <= stop:
            return None
        ei, px = t + 1, o
    else:
        for k in range(t + 1, min(t + 4, n)):
            o, l_ = rows[k][O], rows[k][L]
            if o <= stop:
                return None
            if o <= z1:
                ei, px = k, o
                break
            if l_ <= z1:
                ei, px = k, z1
                break
        if ei is None:
            return None
    if px - stop <= 0:
        return None
    for k in range(ei, min(ei + 10, n)):
        o, h, l_ = rows[k][O], rows[k][H], rows[k][L]
        later = k > ei or entry_mode == "open"
        if later and o <= stop:
            return px, o, "stop", ei
        if l_ <= stop:
            return px, stop, "stop", ei
        if h >= target:
            return px, (max(o, target) if later else target), "target", ei
    if ei + 9 > n - 1:
        return None
    return px, rows[ei + 9][C], "time", ei


def run(stocks, ind, mkt, setup, opt):
    trades = []
    for sym, rows in stocks.items():
        atr, sma20, sma50, vavg, tv = ind[sym]
        for t in range(61, len(rows) - 1):
            if tv[t] is None or tv[t] < 1e8 or atr[t] is None or sma50[t] is None or atr[t] <= 0:
                continue
            A, c = atr[t], rows[t][C]
            if opt.get("market") and not mkt.get(rows[t][D], False):
                continue
            uptrend = c > sma50[t] and sma50[t - 5] is not None and sma50[t] > sma50[t - 5]
            if setup == "breakout":
                prior = rows[t - 20:t]
                lvl = max(x[H] for x in prior)
                if sum(1 for x in prior if x[H] >= lvl * 0.995) < 2:
                    continue
                if not (c > lvl and vavg[t] and rows[t][V] >= 1.5 * vavg[t]):
                    continue
                if opt.get("trend") and not uptrend:
                    continue
                z0, z1 = lvl, lvl + 0.5 * A
                stop = lvl - opt["stop_buf"] * A
                swing_hi = max(x[H] for x in rows[t - 60:t])
            else:  # pullback
                if not uptrend or sma20[t] is None:
                    continue
                touched = any(rows[k][L] <= sma20[k] * 1.005 for k in range(t - 2, t + 1) if sma20[k])
                higher_low = rows[t][L] > min(rows[k][L] for k in range(t - 3, t))
                if not (touched and higher_low and c > rows[t - 1][C] and c > sma20[t]):
                    continue
                z0, z1 = sma20[t] - 0.25 * A, sma20[t] + 0.25 * A
                stop = min(rows[k][L] for k in range(t - 3, t + 1)) - 0.25 * A
                swing_hi = max(x[H] for x in rows[t - 20:t])
                if stop >= z0:
                    continue
            if c - z1 > A:
                continue
            mid = (z0 + z1) / 2 if opt["entry"] == "retest" else c
            cands = [mid + 3 * A] + ([swing_hi] if swing_hi > z1 + 0.1 * A else [])
            target = min(cands)
            if mid - stop <= 0 or (target - mid) / (mid - stop) < 2:
                continue
            res = simulate(rows, t, z0, z1, stop, target, opt["entry"])
            if not res:
                continue
            px, ex, kind, ei = res
            risk = px - stop
            gross = (ex - px) / risk
            trades.append({"sym": sym, "signal": rows[t][D], "entry": rows[ei][D],
                           "exit_i": min(ei + 9, len(rows) - 1), "exit": rows[min(ei + 9, len(rows) - 1)][D] if kind == "time" else None,
                           "r": gross - COST * px / risk, "gross": gross, "kind": kind, "risk_pct": 100 * risk / px})
    return trades


def summarise(tr):
    if not tr:
        return {"trades": 0}
    rs = [x["r"] for x in tr]
    w = [r for r in rs if r > 0]
    m = st.mean(rs)
    sd = st.pstdev(rs) if len(rs) > 1 else 0
    return {"trades": len(rs), "win_pct": round(100 * len(w) / len(rs), 1), "exp_r": round(m, 3),
            "exp_r_before_costs": round(st.mean(x["gross"] for x in tr), 3),
            "avg_win_r": round(st.mean(w), 2) if w else None,
            "avg_loss_r": round(st.mean([r for r in rs if r <= 0]), 2) if len(w) < len(rs) else None,
            "t_stat": round(m / (sd / math.sqrt(len(rs))), 2) if sd else None,
            "stop_pct": round(st.mean(x["risk_pct"] for x in tr), 2)}


def portfolio(tr, max_open=4):
    """Take signals in entry-date order, at most max_open at a time, 1% risk each. -> (total R, worst drawdown R, taken)."""
    taken, open_until, cum, peak, dd = [], [], 0.0, 0.0, 0.0
    for x in sorted(tr, key=lambda x: (x["entry"], x["sym"])):
        open_until = [d for d in open_until if d >= x["entry"]]
        if len(open_until) >= max_open:
            continue
        # exits are within 10 sessions; approximate the exit date as entry + 14 calendar days
        open_until.append((dt.date.fromisoformat(x["entry"]) + dt.timedelta(days=14)).isoformat())
        taken.append(x)
        cum += x["r"]
        peak = max(peak, cum)
        dd = min(dd, cum - peak)
    return round(cum, 1), round(dd, 1), len(taken)


def svg_curves(curves, path, title):
    W, H_, L_, R_, T_, B_ = 900, 380, 56, 120, 34, 40
    allx = sorted({d for _, pts in curves for d, _ in pts})
    if not allx:
        return
    x0, x1 = dt.date.fromisoformat(allx[0]), dt.date.fromisoformat(allx[-1])
    ys = [y for _, pts in curves for _, y in pts] + [0]
    y0, y1 = min(ys), max(ys)
    pad = (y1 - y0) * 0.08 or 1
    y0, y1 = y0 - pad, y1 + pad
    X = lambda d: L_ + (W - L_ - R_) * ((dt.date.fromisoformat(d) - x0).days / max(1, (x1 - x0).days))
    Y = lambda v: T_ + (H_ - T_ - B_) * (1 - (v - y0) / (y1 - y0))
    colors = ["#1f6f8b", "#c2413b", "#b7791f", "#1b8a5a", "#6b4fa0", "#5d6b78"]
    out = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d" font-family="Helvetica,Arial,sans-serif">' % (W, H_, W, H_),
           '<rect width="100%" height="100%" fill="#ffffff"/>',
           '<text x="%d" y="22" font-size="15" font-weight="600" fill="#17212b">%s</text>' % (L_, title)]
    raw = (y1 - y0) / 5
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    v = math.ceil(y0 / step) * step
    while v <= y1:
        out.append('<line x1="%d" x2="%d" y1="%.1f" y2="%.1f" stroke="%s"/>' % (L_, W - R_, Y(v), Y(v), "#9aa5ae" if v == 0 else "#eceff2"))
        out.append('<text x="%d" y="%.1f" font-size="11" text-anchor="end" fill="#5d6b78">%+gR</text>' % (L_ - 6, Y(v) + 4, v))
        v += step
    for yr in range(x0.year + 1, x1.year + 1):
        d = "%d-01-01" % yr
        out.append('<line x1="%.1f" x2="%.1f" y1="%d" y2="%d" stroke="#eceff2"/>' % (X(d), X(d), T_, H_ - B_))
        out.append('<text x="%.1f" y="%d" font-size="11" text-anchor="middle" fill="#5d6b78">%d</text>' % (X(d), H_ - B_ + 16, yr))
    sx = X(a.split)
    out.append('<line x1="%.1f" x2="%.1f" y1="%d" y2="%d" stroke="#5d6b78" stroke-dasharray="4 4"/>' % (sx, sx, T_, H_ - B_))
    out.append('<text x="%.1f" y="%d" font-size="11" fill="#5d6b78">held-out period</text>' % (sx + 4, T_ + 12))
    labels = []
    for i, (name, pts) in enumerate(curves):
        if not pts:
            continue
        col = colors[i % len(colors)]
        d = " ".join(("M" if j == 0 else "L") + "%.1f %.1f" % (X(p), Y(y)) for j, (p, y) in enumerate(pts))
        out.append('<path d="%s" fill="none" stroke="%s" stroke-width="2"/>' % (d, col))
        labels.append([Y(pts[-1][1]), col, "%s %+.0fR" % (name, pts[-1][1])])
    labels.sort()
    for k in range(1, len(labels)):  # keep end labels at least 15px apart
        labels[k][0] = max(labels[k][0], labels[k - 1][0] + 15)
    for ly, col, txt in labels:
        out.append('<text x="%d" y="%.1f" font-size="12" font-weight="600" fill="%s">%s</text>' % (W - R_ + 8, ly + 4, col, txt))
    out.append("</svg>")
    open(path, "w").write("\n".join(out))


def main():
    stocks, idx = load()
    ind = {s: prep(r) for s, r in stocks.items()}
    mkt = nifty_ok(idx)
    os.makedirs(a.out, exist_ok=True)
    first, last = idx[0][D], idx[-1][D]
    nifty_chg = 100 * (idx[-1][C] / idx[0][C] - 1)
    split_i = next(i for i, r in enumerate(idx) if r[D] >= a.split)
    results, curves = [], []
    for code, desc, setup, opt in VARIANTS:
        tr = run(stocks, ind, mkt, setup, opt)
        ins = [x for x in tr if x["signal"] < a.split]
        oos = [x for x in tr if x["signal"] >= a.split]
        years = {}
        for x in tr:
            years.setdefault(x["signal"][:4], []).append(x)
        tot, dd, taken = portfolio(tr)
        results.append({"code": code, "setup": setup, "rules": desc, "options": opt,
                        "all": summarise(tr), "in_sample": summarise(ins), "out_of_sample": summarise(oos),
                        "by_year": {y: summarise(v) for y, v in sorted(years.items())},
                        "portfolio_4_open": {"total_r": tot, "worst_drawdown_r": dd, "trades_taken": taken}})
        pts, cum = [], 0.0
        open_until = []
        for x in sorted(tr, key=lambda x: (x["entry"], x["sym"])):
            open_until = [d for d in open_until if d >= x["entry"]]
            if len(open_until) >= 4:
                continue
            open_until.append((dt.date.fromisoformat(x["entry"]) + dt.timedelta(days=14)).isoformat())
            cum += x["r"]
            pts.append((x["entry"], cum))
        curves.append((code, pts))
        print("%s %-9s all %s | in %s | out %s | portfolio %+.1fR dd %.1fR" % (
            code, setup, results[-1]["all"].get("exp_r"), results[-1]["in_sample"].get("exp_r"),
            results[-1]["out_of_sample"].get("exp_r"), tot, dd), flush=True)
    stamp = idx[-1][D]
    meta = {"generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "data_from": first, "data_to": last,
            "split": a.split, "stocks": len(stocks), "nifty_change_pct": round(nifty_chg, 1),
            "nifty_in_sample_pct": round(100 * (idx[split_i - 1][C] / idx[0][C] - 1), 1),
            "nifty_out_of_sample_pct": round(100 * (idx[-1][C] / idx[split_i][C] - 1), 1), "cost_round_trip_pct": COST * 100}
    json.dump({"meta": meta, "results": results}, open(os.path.join(a.out, "backtest-%s.json" % stamp), "w"), indent=1)
    svg_curves(curves, os.path.join(a.out, "equity-%s.svg" % stamp),
               "Cumulative R, at most 4 trades open, 1%% risk each, after costs (%s to %s)" % (first, last))
    print("saved -> %s" % a.out)
    return meta, results, stamp




if __name__ == "__main__":
    main()
