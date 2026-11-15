#!/usr/bin/env python3
"""trade-sentinel: the code half of the five-engine 24/7 trading research system.

Standard library only. Python 3.9+. Code does the maths; Claude plays the five
engines (SCAN, SIGNAL, PLAN, RISK, MONITOR) from prompts/; a human places
every order. Nothing here ever talks to a broker.

    python3 engine.py init [--preset nse]          # create trader-247/ with config, rules, account, universe
    python3 engine.py fetch                        # 1y daily bars for the universe (free Yahoo chart JSON)
    python3 engine.py prepare                      # new cycle: indicators + code triggers -> scan_input.json
    python3 engine.py validate scan                # check Claude's scan.json -> scan.valid.json
    python3 engine.py signal-input                 # candidates + 60 bars + SMA/ATR -> signal_input.json
    python3 engine.py validate signal
    python3 engine.py plan-input                   # PASS signals + bars -> plan_input.json
    python3 engine.py validate plan                # recomputes rr, rejects a mismatch > 0.05
    python3 engine.py risk                         # code gates G1-G5 -> risk_checks.json + risk_input.json
    python3 engine.py validate risk                # Claude's second opinion
    python3 engine.py card                         # decision cards for plans BOTH call APPROVED
    python3 engine.py decide NOVA EXECUTE --fill 118.52 --why "clean level, volume confirmed"
    python3 engine.py decide NOVA WATCH --why "wait for the retest"
    python3 engine.py decide NOVA PASS  --why "too close to earnings"
    python3 engine.py monitor                      # price alerts in code -> monitor_input.json
    python3 engine.py validate monitor             # Claude's alerts; records alerts_sent
    python3 engine.py notify                       # HIGH alerts to Telegram (if set), others batched
    python3 engine.py fill NOVA 118.55             # a WATCH/WAITING item you entered -> OPEN
    python3 engine.py move-stop NOVA 116.00        # stops move one way only (rule 11)
    python3 engine.py close NOVA 128.10            # CLOSED, result in R written to the journal
    python3 engine.py review                       # journal by setup and score band, go-live checklist
    python3 engine.py status

Every command takes --dir (default trader-247) and --cycle (default: the latest).
"""
import argparse
import csv
import datetime as dt
import json
import math
import os
import random
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
UA = "Mozilla/5.0 (compatible; trade-sentinel/1.0)"  # Yahoo answers a fake Chrome UA with HTTP 429
YAHOO_HOSTS = ("https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com")
VERSION = "1.0"

SETUPS = {"BREAKOUT", "PULLBACK", "MOMENTUM", "TREND_CONT", "REVERSAL", "NONE"}
ALERT_TYPES = {"ENTRY_ZONE_REACHED", "TARGET_HIT", "STOP_HIT", "INVALIDATED", "NEWS", "MARKET_SHIFT", "TIME_STOP"}
DEFAULT_RULES = json.load(open(os.path.join(HERE, "risk_rules.example.json")))
DEFAULT_CONFIG = json.load(open(os.path.join(HERE, "config.example.json")))


# --------------------------------------------------------------------------- io
def now_utc():
    return dt.datetime.now(dt.timezone.utc)


def iso(t):
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)


def save(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2)
    os.replace(tmp, path)


def envelope(cycle_id, engine, items, warnings=None):
    return {"cycle_id": cycle_id, "engine": engine, "version": VERSION,
            "generated_at": iso(now_utc()), "items": items, "warnings": warnings or []}


class Ctx:
    def __init__(self, a):
        self.dir = a.dir
        self.config = {**DEFAULT_CONFIG, **load(os.path.join(a.dir, "config.json"), {})}
        self.rules = {**DEFAULT_RULES, **load(os.path.join(a.dir, "risk_rules.json"), {})}
        self.account = load(os.path.join(a.dir, "account.json"), {"equity": 0})
        uni = load(os.path.join(a.dir, "universe.json"), [])
        self.universe = [u if isinstance(u, dict) else {"symbol": u} for u in uni]
        self.sector = {u["symbol"]: u.get("sector") for u in self.universe}
        self.cycle = getattr(a, "cycle", None) or self._latest_cycle()

    def p(self, *parts):
        return os.path.join(self.dir, *parts)

    def cp(self, name):
        if not self.cycle:
            die("no cycle yet: run `engine.py prepare` first")
        return self.p("cycles", self.cycle, name)

    def _latest_cycle(self):
        cur = self.p("current_cycle")
        return open(cur).read().strip() if os.path.exists(cur) else None

    def bars(self, sym):
        return load(self.p("bars", safe(sym) + ".json"), [])


def safe(sym):
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in sym)


def die(msg):
    print("ERROR: " + msg, file=sys.stderr)
    sys.exit(2)


def r2(x):
    return None if x is None else round(float(x), 2)


# ----------------------------------------------------------------------- data
def http_json(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def yahoo_json(path):
    """query1, then query2 on a 429 / 5xx; one more round after 5 s if a 429 was seen."""
    last = None
    for attempt in (0, 1):
        saw_429 = False
        for host in YAHOO_HOSTS:
            try:
                return http_json(host + path)
            except urllib.error.HTTPError as e:
                last = e
                if e.code == 429:
                    saw_429 = True
                elif e.code < 500:
                    raise
        if not saw_429:
            break
        time.sleep(5)
    raise last


def yahoo_bars(sym, rng="1y", interval="1d"):
    path = "/v8/finance/chart/%s?range=%s&interval=%s" % (urllib.parse.quote(sym), rng, interval)
    res = yahoo_json(path)["chart"]["result"][0]
    meta = res.get("meta", {})
    off = meta.get("gmtoffset", 0) or 0
    q = res["indicators"]["quote"][0]
    out = []
    for i, ts in enumerate(res.get("timestamp") or []):
        o, h, l, c, v = (q[k][i] for k in ("open", "high", "low", "close", "volume"))
        if None in (o, h, l, c):
            continue
        d = dt.datetime.fromtimestamp(ts + off, dt.timezone.utc).strftime("%Y-%m-%d")
        out.append({"date": d, "open": r2(o), "high": r2(h), "low": r2(l), "close": r2(c), "volume": int(v or 0)})
    return out, meta


def load_csv_bars(path):
    rows = []
    with open(path) as f:
        for r in csv.DictReader(f):
            k = {x.lower(): x for x in r}
            rows.append({"date": r[k["date"]][:10], "open": float(r[k["open"]]), "high": float(r[k["high"]]),
                         "low": float(r[k["low"]]), "close": float(r[k["close"]]),
                         "volume": int(float(r[k.get("volume", "volume")] or 0)) if "volume" in k else 0})
    return rows


# ----------------------------------------------------------------- indicators
def sma(vals, n):
    return sum(vals[-n:]) / n if len(vals) >= n else None


def atr_series(bars, n=14):
    trs = []
    for i, b in enumerate(bars):
        pc = bars[i - 1]["close"] if i else b["close"]
        trs.append(max(b["high"] - b["low"], abs(b["high"] - pc), abs(b["low"] - pc)))
    out, a = [], None
    for i, tr in enumerate(trs):
        if i + 1 < n:
            out.append(None)
        elif i + 1 == n:
            a = sum(trs[:n]) / n
            out.append(a)
        else:
            a = (a * (n - 1) + tr) / n  # Wilder
            out.append(a)
    return out


def pct(a, b):
    return None if a is None or not b else (a - b) / b * 100


def metrics(bars):
    """Everything the SCAN and SIGNAL engines get, computed in code."""
    if len(bars) < 21:
        return None
    c = [b["close"] for b in bars]
    v = [b["volume"] for b in bars]
    last = c[-1]
    atrs = atr_series(bars)
    atr = atrs[-1]
    recent = [x for x in atrs[-20:] if x is not None]
    atr_avg = sum(recent) / len(recent) if len(recent) == 20 else None
    vol_avg = sum(v[-21:-1]) / 20 if len(v) >= 21 else None
    moves = [abs(pct(c[i], c[i - 1])) for i in range(max(1, len(c) - 20), len(c))]
    hi20, lo20 = max(b["high"] for b in bars[-20:]), min(b["low"] for b in bars[-20:])
    hi50 = max(b["high"] for b in bars[-50:]) if len(bars) >= 50 else None
    lo50 = min(b["low"] for b in bars[-50:]) if len(bars) >= 50 else None
    s50 = sma(c, 50)
    s50_prev = sma(c[:-5], 50) if len(c) >= 55 else None
    return {
        "date": bars[-1]["date"], "last": r2(last),
        "chg_1d_pct": r2(pct(c[-1], c[-2])), "chg_5d_pct": r2(pct(c[-1], c[-6])) if len(c) >= 6 else None,
        "volume_ratio": r2(v[-1] / vol_avg) if vol_avg else None,
        "atr14": r2(atr), "atr_ratio": r2(atr / atr_avg) if atr and atr_avg else None,
        "sma20": r2(sma(c, 20)), "sma50": r2(s50),
        "sma50_rising": (s50 > s50_prev) if s50 and s50_prev else None,
        "dist_20d_high_pct": r2(pct(last, hi20)), "dist_20d_low_pct": r2(pct(last, lo20)),
        "dist_50d_high_pct": r2(pct(last, hi50)), "dist_50d_low_pct": r2(pct(last, lo50)),
        "avg_traded_value": round(sum(b["close"] * b["volume"] for b in bars[-20:]) / 20),
        "avg_daily_move_pct": r2(sum(moves) / len(moves)) if moves else None,
    }


def code_triggers(m):
    """SCAN triggers 1, 2 and 4: free and instant in code."""
    t = []
    if m["volume_ratio"] is not None and m["volume_ratio"] >= 2.0:
        t.append("VOLUME_2X")
    for key, name, side in (("dist_20d_high_pct", "NEAR_20D_HIGH", 1), ("dist_20d_low_pct", "NEAR_20D_LOW", -1),
                            ("dist_50d_high_pct", "NEAR_50D_HIGH", 1), ("dist_50d_low_pct", "NEAR_50D_LOW", -1)):
        d = m.get(key)
        if d is not None and (d * side >= -1.5):  # within 1.5% of, or beyond
            t.append(name)
    if m["chg_1d_pct"] is not None and m["avg_daily_move_pct"]:
        if abs(m["chg_1d_pct"]) >= 2 * m["avg_daily_move_pct"]:
            t.append("BIG_MOVE")
    return t


def weekly(bars, n=12):
    wk = {}
    for b in bars:
        y, w, _ = dt.date.fromisoformat(b["date"]).isocalendar()
        k = "%d-W%02d" % (y, w)
        if k not in wk:
            wk[k] = dict(b, week=k)
        else:
            x = wk[k]
            x["high"], x["low"] = max(x["high"], b["high"]), min(x["low"], b["low"])
            x["close"], x["volume"] = b["close"], x["volume"] + b["volume"]
    out = list(wk.values())[-n:]
    return [{"week": x["week"], "open": x["open"], "high": x["high"], "low": x["low"], "close": x["close"],
             "volume": x["volume"]} for x in out]


# ----------------------------------------------------------------- validation
def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def validate_item(engine, it, ctx):
    """Return a list of problems; empty means the item may pass to the next engine."""
    e = []
    if not isinstance(it, dict):
        return ["item is not an object"]
    if not isinstance(it.get("symbol"), str) or not it["symbol"]:
        e.append("symbol missing")
    if engine == "scan":
        if not isinstance(it.get("triggers"), list) or not it["triggers"]:
            e.append("triggers[] missing or empty")
        for k in ("volume_ratio", "last"):
            if k not in it:
                e.append(k + " missing")
            elif it[k] is not None and not _num(it[k]):
                e.append(k + " not a number")
        if "headline" not in it:
            e.append("headline missing (use null)")
        if not isinstance(it.get("notes", ""), str):
            e.append("notes not a string")
    elif engine == "signal":
        if it.get("setup") not in SETUPS:
            e.append("setup must be one of " + ",".join(sorted(SETUPS)))
        sc = it.get("score")
        if not _num(sc) or not 0 <= sc <= 100:
            e.append("score must be 0-100")
        if it.get("status") not in ("PASS", "FILTERED"):
            e.append("status must be PASS or FILTERED")
        elif _num(sc):
            should = "PASS" if sc >= ctx.config["pass_mark"] and it.get("setup") != "NONE" else "FILTERED"
            if it["status"] != should:
                e.append("status %s but score %s vs pass mark %s / setup %s"
                         % (it["status"], sc, ctx.config["pass_mark"], it.get("setup")))
        lv = it.get("levels")
        if not isinstance(lv, dict) or "support" not in lv or "resistance" not in lv:
            e.append("levels{support,resistance} missing")
        else:
            bars = ctx.bars(it.get("symbol", ""))[-60:]
            if bars:
                lo, hi = min(b["low"] for b in bars), max(b["high"] for b in bars)
                for k in ("support", "resistance"):
                    x = lv[k]
                    if x is not None and (not _num(x) or not lo * 0.995 <= x <= hi * 1.005):
                        e.append("levels.%s %s is not a price in the last 60 bars (%s-%s)" % (k, x, lo, hi))
        if not isinstance(it.get("evidence"), str):
            e.append("evidence missing")
        if it.get("status") == "FILTERED" and not it.get("reason"):
            e.append("FILTERED needs a reason")
    elif engine == "plan":
        st = it.get("status")
        if st not in ("PLANNED", "NO_PLAN"):
            e.append("status must be PLANNED or NO_PLAN")
        if st == "NO_PLAN":
            if not it.get("reason"):
                e.append("NO_PLAN needs a reason")
            return e
        d = it.get("direction")
        if d not in ("LONG", "SHORT"):
            e.append("direction must be LONG or SHORT")
        z = it.get("entry_zone")
        if not (isinstance(z, list) and len(z) == 2 and all(_num(x) for x in z) and z[0] <= z[1]):
            e.append("entry_zone must be [low, high] numbers")
            return e
        s, t = it.get("stop"), it.get("target")
        if not _num(s) or not _num(t):
            e.append("stop and target must be numbers")
            return e
        if d == "LONG" and not (s < z[0] and t > z[1]):
            e.append("LONG needs stop < entry_zone < target")
        if d == "SHORT" and not (s > z[1] and t < z[0]):
            e.append("SHORT needs target < entry_zone < stop")
        mid = (z[0] + z[1]) / 2
        if mid != s:
            rr = abs(t - mid) / abs(mid - s)
            if not _num(it.get("rr")) or abs(rr - it["rr"]) > ctx.config["rr_tolerance"]:
                e.append("rr %s does not match code's %.2f" % (it.get("rr"), rr))
        if not isinstance(it.get("time_stop_bars"), int) or it["time_stop_bars"] < 1:
            e.append("time_stop_bars must be a positive integer")
        cm = it.get("change_my_mind")
        if not isinstance(cm, list) or not 1 <= len(cm) <= 3:
            e.append("change_my_mind must list 1-3 events")
        for k in ("invalidation", "thesis"):
            if not isinstance(it.get(k), str) or not it[k]:
                e.append(k + " missing")
    elif engine == "risk":
        if it.get("verdict") not in ("APPROVED", "BLOCKED"):
            e.append("verdict must be APPROVED or BLOCKED")
        if not isinstance(it.get("gates"), list):
            e.append("gates[] missing")
        if not isinstance(it.get("reason"), str):
            e.append("reason missing")
    elif engine == "monitor":
        if it.get("type") not in ALERT_TYPES:
            e.append("type must be one of " + ",".join(sorted(ALERT_TYPES)))
        if it.get("priority") not in ("HIGH", "MEDIUM", "LOW"):
            e.append("priority must be HIGH, MEDIUM or LOW")
        if it.get("thesis_status") not in ("VALID", "WEAKENING", "BROKEN"):
            e.append("thesis_status must be VALID, WEAKENING or BROKEN")
        m = it.get("message")
        if not isinstance(m, str) or not m or len(m) >= 240:
            e.append("message must be 1-239 characters")
    return e


def cmd_validate(ctx, a):
    engine = a.engine
    path = a.file or ctx.cp(engine + ".json")
    try:
        raw = open(path).read().strip()
        if raw.startswith("```"):  # tolerate a fenced reply, nothing else
            raw = raw.split("\n", 1)[1] if "\n" in raw else ""
            raw = raw.rsplit("```", 1)[0]
        env = json.loads(raw)
    except FileNotFoundError:
        die("%s not found: save the %s engine's JSON there first" % (path, engine.upper()))
    except json.JSONDecodeError as ex:
        die("%s does not parse as JSON (%s). The cycle stops here; re-run the engine." % (path, ex))
    probs = []
    for k in ("cycle_id", "engine", "version", "generated_at", "items", "warnings"):
        if k not in env:
            probs.append("envelope field %s missing" % k)
    if env.get("engine") != engine:
        probs.append("envelope engine is %r, expected %r" % (env.get("engine"), engine))
    if env.get("cycle_id") and ctx.cycle and env["cycle_id"] != ctx.cycle:
        probs.append("cycle_id %s is not the current cycle %s" % (env["cycle_id"], ctx.cycle))
    if probs and not isinstance(env.get("items"), list):
        die("; ".join(probs))
    good, dropped = [], []
    for it in env.get("items", []):
        e = validate_item(engine, it, ctx)
        (dropped if e else good).append({"item": it, "problems": e} if e else it)
    if engine == "scan":
        good = good[: ctx.config["max_candidates"]]
    out = envelope(ctx.cycle, engine, good, list(env.get("warnings", [])) + probs +
                   ["DROPPED %s: %s" % (d["item"].get("symbol") if isinstance(d["item"], dict) else "?",
                                       "; ".join(d["problems"])) for d in dropped])
    save(ctx.cp(engine + ".valid.json"), out)
    if engine == "monitor":
        record_alerts(ctx, good)
    print("%s: %d valid, %d dropped -> %s" % (engine, len(good), len(dropped), ctx.cp(engine + ".valid.json")))
    for w in out["warnings"]:
        print("  ! " + w)


# ------------------------------------------------------------------ commands
def cmd_init(ctx, a):
    os.makedirs(ctx.p("bars"), exist_ok=True)
    pre = os.path.join(HERE, "presets", a.preset) if a.preset else None
    if pre and not os.path.isdir(pre):
        die("no preset %r (have: %s)" % (a.preset, ", ".join(sorted(os.listdir(os.path.join(HERE, "presets"))))))
    for src, dst in (("config.example.json", "config.json"), ("risk_rules.example.json", "risk_rules.json"),
                     ("account.example.json", "account.json"), ("universe.example.json", "universe.json")):
        if pre and os.path.exists(os.path.join(pre, dst)):
            src = os.path.join(pre, dst)
        if not os.path.exists(ctx.p(dst)):
            shutil.copy(os.path.join(HERE, src), ctx.p(dst))
            print("created " + ctx.p(dst))
        else:
            print("kept    " + ctx.p(dst))
    for name, empty in (("headlines.json", {}), ("events.json", {}), ("watchlist.json", [])):
        if not os.path.exists(ctx.p(name)):
            save(ctx.p(name), empty)
    print("Edit universe.json (50-300 liquid symbols, one market), account.json (equity) and risk_rules.json.")


def cmd_fetch(ctx, a):
    syms = a.symbols.split(",") if a.symbols else [u["symbol"] for u in ctx.universe]
    idx = ctx.config.get("index_symbol")
    if idx and idx not in syms:
        syms.append(idx)
    ok = 0
    for s in syms:
        if a.csv:
            path = os.path.join(a.csv, safe(s) + ".csv")
            if not os.path.exists(path):
                print("  %-12s no CSV at %s" % (s, path))
                continue
            bars = load_csv_bars(path)
        else:
            try:
                bars, _ = yahoo_bars(s, a.range)
            except Exception as ex:  # one bad symbol never stops the run
                print("  %-12s fetch failed: %s" % (s, ex))
                continue
            time.sleep(a.pause)
        save(ctx.p("bars", safe(s) + ".json"), bars)
        ok += 1
        print("  %-12s %d bars to %s" % (s, len(bars), bars[-1]["date"] if bars else "-"))
    print("fetched %d/%d" % (ok, len(syms)))


def cmd_prepare(ctx, a):
    t = now_utc()
    cid = t.strftime("%Y-%m-%dT%H:%MZ") + "-%04d" % random.randint(0, 9999)
    os.makedirs(ctx.p("cycles", cid), exist_ok=True)
    with open(ctx.p("current_cycle"), "w") as f:
        f.write(cid)
    ctx.cycle = cid
    heads = load(ctx.p("headlines.json"), {})
    allm, short, warn = {}, [], []
    for u in ctx.universe:
        s = u["symbol"]
        m = metrics(ctx.bars(s))
        if not m:
            warn.append("%s: fewer than 21 bars, skipped (run fetch)" % s)
            continue
        if m["avg_traded_value"] < ctx.config["min_liquidity"]:
            continue
        m["symbol"], m["sector"] = s, u.get("sector")
        m["code_triggers"] = code_triggers(m)
        m["headlines"] = heads.get(s, [])
        allm[s] = m
        if m["code_triggers"] or m["headlines"]:
            short.append(m)
    short.sort(key=lambda m: (-len(m["code_triggers"]), -(m["volume_ratio"] or 0)))
    save(ctx.cp("metrics.json"), allm)
    save(ctx.cp("scan_input.json"), {"cycle_id": cid, "engine_prompt": "prompts/scan.md",
                                     "MIN_LIQUIDITY": ctx.config["min_liquidity"],
                                     "MAX_CANDIDATES": ctx.config["max_candidates"],
                                     "universe": short, "warnings": warn})
    print("cycle %s: %d symbols with data, %d on the shortlist (code triggers or headlines)"
          % (cid, len(allm), len(short)))
    for m in short[:15]:
        print("  %-12s %-40s vol x%s  1d %s%%" % (m["symbol"], ",".join(m["code_triggers"]) or "headline",
                                                 m["volume_ratio"], m["chg_1d_pct"]))
    for w in warn:
        print("  ! " + w)
    print("next: run the SCAN engine on %s -> save %s, then `validate scan`" % (ctx.cp("scan_input.json"),
                                                                              ctx.cp("scan.json")))


def cmd_signal_input(ctx, a):
    scan = load(ctx.cp("scan.valid.json")) or die("run `validate scan` first")
    allm = load(ctx.cp("metrics.json"), {})
    items = []
    for c in scan["items"]:
        bars = ctx.bars(c["symbol"])
        m = allm.get(c["symbol"]) or metrics(bars) or {}
        items.append({"candidate": c, "bars": bars[-60:], "weekly_bars": weekly(bars),
                      "sma20": m.get("sma20"), "sma50": m.get("sma50"), "sma50_rising": m.get("sma50_rising"),
                      "atr14": m.get("atr14"), "volume_avg20": round(sum(b["volume"] for b in bars[-21:-1]) / 20)
                      if len(bars) >= 21 else None})
    save(ctx.cp("signal_input.json"), {"cycle_id": ctx.cycle, "engine_prompt": "prompts/signal.md",
                                       "PASS_MARK": ctx.config["pass_mark"], "candidates": items})
    print("%d candidates -> %s ; run SIGNAL, save signal.json, then `validate signal`"
          % (len(items), ctx.cp("signal_input.json")))


def cmd_plan_input(ctx, a):
    sig = load(ctx.cp("signal.valid.json")) or die("run `validate signal` first")
    allm = load(ctx.cp("metrics.json"), {})
    items = []
    for s in sig["items"]:
        if s["status"] != "PASS":
            continue
        bars = ctx.bars(s["symbol"])
        m = allm.get(s["symbol"]) or metrics(bars) or {}
        items.append({"signal": s, "bars": bars[-60:], "atr14": m.get("atr14"),
                      "sma20": m.get("sma20"), "sma50": m.get("sma50"), "last": m.get("last")})
    save(ctx.cp("plan_input.json"), {"cycle_id": ctx.cycle, "engine_prompt": "prompts/plan.md", "signals": items})
    print("%d PASS signals -> %s ; run PLAN, save plan.json, then `validate plan`"
          % (len(items), ctx.cp("plan_input.json")))


# ---------------------------------------------------------------------- risk
def journal_rows(ctx):
    path = ctx.p("journal.jsonl")
    if not os.path.exists(path):
        return []
    return [json.loads(l) for l in open(path) if l.strip()]


def journal_write(ctx, rows):
    path = ctx.p("journal.jsonl")
    with open(path + ".tmp", "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    os.replace(path + ".tmp", path)


def journal_add(ctx, row):
    with open(ctx.p("journal.jsonl"), "a") as f:
        f.write(json.dumps(row) + "\n")


def realised(ctx, t):
    today, wk = t.date().isoformat(), t.date().isocalendar()[:2]
    d = w = 0.0
    for r in journal_rows(ctx):
        if r.get("closed_at") and r.get("pnl") is not None:
            cd = dt.date.fromisoformat(r["closed_at"][:10])
            if cd.isoformat() == today:
                d += r["pnl"]
            if cd.isocalendar()[:2] == wk:
                w += r["pnl"]
    return round(d, 2), round(w, 2)


def compute_risk(ctx, plans, t):
    R, eq = ctx.rules, float(ctx.account.get("equity") or 0)
    allm = load(ctx.cp("metrics.json"), {})
    events = load(ctx.p("events.json"), {})
    watch = load(ctx.p("watchlist.json"), [])
    today_pnl, week_pnl = realised(ctx, t)
    opened = [w for w in watch if w["state"] == "OPEN"]
    open_risk = sum(w["size"] * abs(w["entry_price"] - w["stop"]) for w in opened)
    open_value = sum(w["size"] * w["entry_price"] for w in opened)
    sectors = [w.get("sector") for w in opened]
    active = {w["symbol"] for w in watch if w["state"] in ("WAITING", "OPEN")}
    risk_pct = min(R["risk_per_trade_pct"], R["max_risk_pct"])
    out, warnings = [], []
    if R["risk_per_trade_pct"] > R["max_risk_pct"]:
        warnings.append("risk_per_trade_pct %s is over the hard cap %s; using %s"
                        % (R["risk_per_trade_pct"], R["max_risk_pct"], risk_pct))
    for p in plans:
        sym = p["symbol"]
        z = p["entry_zone"]
        mid = (z[0] + z[1]) / 2
        per_unit = abs(mid - p["stop"])
        size = int((eq * risk_pct / 100) // per_unit) if per_unit and eq > 0 else 0
        risk_amt = round(size * per_unit, 2)
        value = round(size * mid, 2)
        rr = round(abs(p["target"] - mid) / per_unit, 2) if per_unit else 0
        exposure = value / eq * 100 if eq else 999
        o_risk = (open_risk + risk_amt) / eq * 100 if eq else 999
        gross = (open_value + value) / eq if eq else 999
        m = allm.get(sym) or metrics(ctx.bars(sym)) or {}
        atr_ratio = m.get("atr_ratio")
        sec = ctx.sector.get(sym)
        sec_count = sum(1 for s in sectors if s and s == sec)
        ev_hit = []
        for e in events.get(sym, []):
            try:
                et = dt.datetime.fromisoformat(str(e).replace("Z", "+00:00"))
                if et.tzinfo is None:
                    et = et.replace(tzinfo=dt.timezone.utc)
            except ValueError:
                warnings.append("%s: event %r is not an ISO time" % (sym, e))
                continue
            if t <= et <= t + dt.timedelta(hours=R["event_blackout_hours"]):
                ev_hit.append(iso(et))
        loss_today = max(0.0, -today_pnl)
        g = [
            {"id": "G0_DATA", "pass": per_unit > 0 and eq > 0 and sym not in active,
             "value": "already on watchlist" if sym in active else ("ok" if per_unit > 0 and eq > 0 else "bad"),
             "limit": "consistent plan, equity > 0, not already active"},
            {"id": "G0_SHORT", "pass": p["direction"] == "LONG" or bool(ctx.config.get("allow_short", True)),
             "value": p["direction"], "limit": "shorts allowed" if ctx.config.get("allow_short", True)
             else "LONG only (allow_short false: no overnight shorts in this market)"},
            {"id": "G1_SIZE", "pass": size >= 1, "value": size, "limit": ">= 1 at %s%%" % risk_pct},
            {"id": "G2_EXPOSURE", "pass": exposure <= R["max_position_pct"], "value": r2(exposure),
             "limit": R["max_position_pct"]},
            {"id": "G2_OPEN_RISK", "pass": o_risk <= R["max_open_risk_pct"], "value": r2(o_risk),
             "limit": R["max_open_risk_pct"]},
            {"id": "G2_LEVERAGE", "pass": gross <= R["leverage"], "value": r2(gross), "limit": R["leverage"]},
            {"id": "G3_RR", "pass": rr >= R["min_rr"], "value": rr, "limit": R["min_rr"]},
            {"id": "G4_DAILY", "pass": (risk_amt + loss_today) <= eq * R["daily_loss_limit_pct"] / 100,
             "value": r2(risk_amt + loss_today), "limit": r2(eq * R["daily_loss_limit_pct"] / 100)},
            {"id": "G4_WEEKLY", "pass": max(0.0, -week_pnl) < eq * R["weekly_loss_limit_pct"] / 100,
             "value": r2(max(0.0, -week_pnl)), "limit": r2(eq * R["weekly_loss_limit_pct"] / 100)},
            {"id": "G4_POSITIONS", "pass": len(opened) < R["max_positions"], "value": len(opened),
             "limit": "< %s" % R["max_positions"]},
            {"id": "G5_ATR", "pass": atr_ratio is not None and atr_ratio <= R["max_atr_ratio"],
             "value": atr_ratio, "limit": R["max_atr_ratio"]},
            {"id": "G5_EVENT", "pass": not ev_hit, "value": ev_hit or "none",
             "limit": "none within %sh" % R["event_blackout_hours"]},
            {"id": "G5_SECTOR", "pass": sec_count < R["max_per_sector"], "value": sec_count,
             "limit": "< %s (%s)" % (R["max_per_sector"], sec or "no sector")},
        ]
        failed = [x for x in g if not x["pass"]]
        verdict = "BLOCKED" if failed else "APPROVED"
        reason = "All gates pass." if not failed else "; ".join(_why(x) for x in failed)
        out.append({"symbol": sym, "verdict": verdict, "size": size, "risk_amount": risk_amt,
                    "checks": {"entry_mid": r2(mid), "size": size, "risk_amount": risk_amt,
                               "position_value": value, "exposure_pct": r2(exposure),
                               "open_risk_pct": r2(o_risk), "rr": rr, "atr_ratio": atr_ratio,
                               "sector": sec},
                    "gates": g, "reason": reason})
        if verdict == "APPROVED":  # later plans in the batch see this one as taken
            open_risk += risk_amt
            open_value += value
            sectors.append(sec)
            opened = opened + [{"symbol": sym}]
            active.add(sym)
    return out, warnings, {"equity": eq, "realised_pnl_today": today_pnl, "realised_pnl_week": week_pnl,
                           "open_positions": [w for w in watch if w["state"] == "OPEN"]}


def _why(gate):
    names = {"G0_DATA": "DATA", "G0_SHORT": "short not allowed in this market", "G1_SIZE": "size rounds to zero at the risk per trade",
             "G2_EXPOSURE": "position value over the max %", "G2_OPEN_RISK": "open risk over the max %",
             "G2_LEVERAGE": "would need leverage", "G3_RR": "R:R below the minimum",
             "G4_DAILY": "daily loss limit", "G4_WEEKLY": "weekly loss limit hit",
             "G4_POSITIONS": "max open positions", "G5_ATR": "ATR too high vs normal",
             "G5_EVENT": "event inside the blackout", "G5_SECTOR": "sector already full"}
    return "%s %s (%s vs %s)" % (gate["id"], names.get(gate["id"], ""), gate["value"], gate["limit"])


def cmd_risk(ctx, a):
    plan = load(ctx.cp("plan.valid.json")) or die("run `validate plan` first")
    t = now_utc() if not a.now else dt.datetime.fromisoformat(a.now.replace("Z", "+00:00")).astimezone(dt.timezone.utc)
    plans = [p for p in plan["items"] if p.get("status") == "PLANNED"]
    verdicts, warn, acct = compute_risk(ctx, plans, t)
    save(ctx.cp("risk_checks.json"), envelope(ctx.cycle, "risk", verdicts, warn))
    allm = load(ctx.cp("metrics.json"), {})
    save(ctx.cp("risk_input.json"), {
        "cycle_id": ctx.cycle, "engine_prompt": "prompts/risk.md", "plans": plans, "account": acct,
        "market": {"atr_ratio": {p["symbol"]: (allm.get(p["symbol"]) or {}).get("atr_ratio") for p in plans},
                   "events": load(ctx.p("events.json"), {})},
        "risk_rules": ctx.rules, "checks": [dict(v["checks"], symbol=v["symbol"]) for v in verdicts]})
    sig = {s["symbol"]: s for s in (load(ctx.cp("signal.valid.json")) or {"items": []})["items"]}
    for v in verdicts:
        mark = "APPROVED" if v["verdict"] == "APPROVED" else "BLOCKED "
        print("%s %-10s size %-6s risk %-9s %s" % (mark, v["symbol"], v["size"], v["risk_amount"], v["reason"]))
        if v["verdict"] == "BLOCKED" and not a.no_journal:
            s = sig.get(v["symbol"], {})
            journal_add(ctx, {"id": "%s:%s" % (ctx.cycle, v["symbol"]), "date": t.date().isoformat(),
                              "cycle_id": ctx.cycle, "symbol": v["symbol"], "setup": s.get("setup"),
                              "score": s.get("score"), "rr": v["checks"]["rr"], "decision": "Blocked",
                              "why": v["reason"], "result_r": None})
    for w in warn:
        print("  ! " + w)
    print("code verdicts -> %s ; RISK engine input -> %s (save risk.json, then `validate risk`)"
          % (ctx.cp("risk_checks.json"), ctx.cp("risk_input.json")))


# ---------------------------------------------------------- card and decide
def approved(ctx, code_only=False):
    code = load(ctx.cp("risk_checks.json")) or die("run `risk` first")
    claude = load(ctx.cp("risk.valid.json"))
    if claude is None and not code_only:
        die("no risk.valid.json: run the RISK engine and `validate risk` (or pass --code-only)")
    votes = {v["symbol"]: v["verdict"] for v in (claude or {"items": []})["items"]}
    plans = {p["symbol"]: p for p in load(ctx.cp("plan.valid.json"))["items"]}
    sigs = {s["symbol"]: s for s in (load(ctx.cp("signal.valid.json")) or {"items": []})["items"]}
    out = []
    for v in code["items"]:
        both = v["verdict"] == "APPROVED" and (code_only or votes.get(v["symbol"]) == "APPROVED")
        if v["verdict"] == "APPROVED" and not both:
            print("held back %s: code APPROVED but the RISK engine said %s" % (v["symbol"], votes.get(v["symbol"])))
        if both:
            out.append((v, plans[v["symbol"]], sigs.get(v["symbol"], {})))
    return out


def card_text(v, p, s, eq, cur=""):
    z = p["entry_zone"]
    groups = sorted({g["id"].split("_")[0] for g in v["gates"] if not g["id"].startswith("G0")})
    passed = sum(1 for gr in groups if all(g["pass"] for g in v["gates"] if g["id"].startswith(gr + "_")))
    arrow = "▲" if p["direction"] == "LONG" else "▼"
    risk_pct = v["risk_amount"] / eq * 100 if eq else 0
    return "\n".join([
        "%s %s · %s · %s        score %s" % (arrow, p["symbol"], s.get("setup", "?"), p["direction"], s.get("score", "?")),
        "entry   %s – %s" % (z[0], z[1]),
        "stop    %-10s target  %s" % (p["stop"], p["target"]),
        "R:R     1 : %-6.2f size    %s units" % (v["checks"]["rr"], v["size"]),
        "risk    %s%s (%.1f%%)   gates  %d / %d pass" % (cur, v["risk_amount"], risk_pct, passed, len(groups)),
        "",
        "thesis  " + p["thesis"],
        "wrong if " + p["invalidation"],
        "",
        "reply:  EXECUTE · WATCH · PASS",
    ])


def cmd_card(ctx, a):
    eq = float(ctx.account.get("equity") or 0)
    cards = []
    for v, p, s in approved(ctx, a.code_only):
        txt = card_text(v, p, s, eq, ctx.account.get("currency_symbol", ""))
        cards.append({"symbol": p["symbol"], "card": txt})
        print(txt)
        print("-" * 60)
    save(ctx.cp("cards.json"), cards)
    if not cards:
        print("No plan was approved by both code and the RISK engine this cycle. Nothing to decide.")
    else:
        print("Answer each with: engine.py decide SYMBOL EXECUTE|WATCH|PASS --why \"...\" [--fill PRICE]")
    if a.telegram and cards:
        for c in cards:
            telegram(c["card"])


def cmd_decide(ctx, a):
    choice = a.choice.upper()
    if choice not in ("EXECUTE", "WATCH", "PASS"):
        die("answer EXECUTE, WATCH or PASS")
    found = [x for x in approved(ctx, True) if x[1]["symbol"] == a.symbol]
    if not found:
        die("%s has no approved plan in cycle %s" % (a.symbol, ctx.cycle))
    v, p, s = found[0]
    t = now_utc()
    rid = "%s:%s" % (ctx.cycle, a.symbol)
    rows = [r for r in journal_rows(ctx) if r["id"] != rid]
    rows.append({"id": rid, "date": t.date().isoformat(), "cycle_id": ctx.cycle, "symbol": a.symbol,
                 "setup": s.get("setup"), "score": s.get("score"), "rr": v["checks"]["rr"],
                 "decision": choice.title(), "why": a.why or "", "result_r": "open" if choice == "EXECUTE" else None})
    journal_write(ctx, rows)
    if choice == "PASS":
        print("%s archived with score %s. No trade is also a position." % (a.symbol, s.get("score")))
        return
    watch = [w for w in load(ctx.p("watchlist.json"), []) if not (w["symbol"] == a.symbol and w["state"] != "CLOSED")]
    item = {"symbol": a.symbol, "sector": ctx.sector.get(a.symbol), "direction": p["direction"],
            "entry_zone": p["entry_zone"], "stop": p["stop"], "initial_stop": p["stop"], "target": p["target"],
            "invalidation": p["invalidation"], "change_my_mind": p["change_my_mind"], "thesis": p["thesis"],
            "time_stop_bars": p["time_stop_bars"], "planned_date": t.date().isoformat(), "size": v["size"],
            "journal_id": rid, "alerts_sent": [], "state": "WAITING", "entry_price": None}
    if choice == "EXECUTE":
        item["state"] = "OPEN"
        item["entry_price"] = a.fill if a.fill is not None else v["checks"]["entry_mid"]
        item["opened_at"] = iso(t)
    watch.append(item)
    save(ctx.p("watchlist.json"), watch)
    print("%s -> %s%s. Journal row written." % (a.symbol, item["state"],
                                               " at %s (you place the order yourself)" % item["entry_price"]
                                               if choice == "EXECUTE" else ""))


# ------------------------------------------------------------------- monitor
def _active(w, price):
    """Which price conditions are true right now for this item: {type: level}."""
    z, d = w["entry_zone"], w["direction"]
    act = {}
    if w["state"] == "WAITING" and z[0] <= price <= z[1]:
        act["ENTRY_ZONE_REACHED"] = z[0] if d == "LONG" else z[1]
    if w["state"] == "OPEN":
        if (d == "LONG" and price >= w["target"]) or (d == "SHORT" and price <= w["target"]):
            act["TARGET_HIT"] = w["target"]
        if (d == "LONG" and price <= w["stop"]) or (d == "SHORT" and price >= w["stop"]):
            act["STOP_HIT"] = w["stop"]
    return act


def cmd_monitor(ctx, a):
    watch = load(ctx.p("watchlist.json"), [])
    live = [w for w in watch if w["state"] in ("WAITING", "OPEN")]
    prices = load(a.prices, {}) if a.prices else {}
    heads = load(ctx.p("headlines.json"), {})
    t = now_utc()
    idx_move, code_alerts, view = None, [], []
    idx = ctx.config.get("index_symbol")
    if idx:
        try:
            if idx in prices:
                ib = ctx.bars(idx)
                idx_move = r2(pct(prices[idx], ib[-1]["close"])) if ib else None
            else:
                ib, _ = yahoo_bars(idx, "5d")
                idx_move = r2(pct(ib[-1]["close"], ib[-2]["close"])) if len(ib) >= 2 else None
        except Exception as ex:
            print("  ! index %s: %s" % (idx, ex))
    for w in live:
        s = w["symbol"]
        recent = []
        try:
            if s in prices:
                price = float(prices[s])
                recent = ctx.bars(s)[-5:]
            else:
                recent, meta = yahoo_bars(s, "3mo")
                price = meta.get("regularMarketPrice") or recent[-1]["close"]
        except Exception as ex:
            print("  ! %s: no price (%s)" % (s, ex))
            continue
        since = [b for b in (recent if s not in prices else ctx.bars(s)) if b["date"] > w["planned_date"]]
        act = _active(w, price)
        if w["state"] == "WAITING" and len(since) >= w["time_stop_bars"]:
            act["TIME_STOP"] = w["time_stop_bars"]
        if idx_move is not None and abs(idx_move) >= ctx.config["market_shock_pct"]:
            act["MARKET_SHIFT"] = "%s:%s" % (idx, t.date().isoformat())
        # re-arm price levels the price has left (one alert per crossing)
        w["alerts_sent"] = [x for x in w["alerts_sent"]
                            if x["type"] in ("TIME_STOP", "MARKET_SHIFT", "NEWS", "INVALIDATED") or x["type"] in act]
        sent = {(x["type"], str(x["level"])) for x in w["alerts_sent"]}
        for typ, lvl in act.items():
            if (typ, str(lvl)) in sent:
                continue
            pri = "HIGH" if typ == "STOP_HIT" else "MEDIUM"
            code_alerts.append({"symbol": s, "type": typ, "priority": pri, "price": r2(price), "level": lvl,
                                "message": _msg(w, typ, price, lvl, idx_move)})
        w["last_price"], w["last_check"] = r2(price), iso(t)
        view.append({"symbol": s, "state": w["state"], "direction": w["direction"], "entry_zone": w["entry_zone"],
                     "stop": w["stop"], "target": w["target"], "entry_price": w["entry_price"],
                     "invalidation": w["invalidation"], "change_my_mind": w["change_my_mind"],
                     "latest_price": r2(price), "recent_bars": recent[-5:], "bars_since_plan": len(since),
                     "headlines": heads.get(s, []), "alerts_sent": w["alerts_sent"]})
    save(ctx.p("watchlist.json"), watch)
    payload = {"cycle_id": ctx.cycle, "engine_prompt": "prompts/monitor.md", "checked_at": iso(t),
               "INTERVAL": ctx.config["monitor_interval_min"], "MARKET_SHOCK_PCT": ctx.config["market_shock_pct"],
               "index_move_today_pct": idx_move, "watchlist": view, "code_alerts": code_alerts}
    if ctx.cycle:
        save(ctx.cp("monitor_input.json"), payload)
    print("monitor %s: %d live items, index %s%%, %d code alerts"
          % (iso(t), len(view), idx_move, len(code_alerts)))
    for c in code_alerts:
        print("  [%s] %s" % (c["priority"], c["message"]))
    if a.commit:  # no Claude in the loop: record and pass the code alerts on as-is
        items = [dict(c, thesis_status="VALID") for c in code_alerts]
        save(ctx.cp("monitor.valid.json"), envelope(ctx.cycle, "monitor", items, ["code-only monitor"]))
        record_alerts(ctx, items)
    elif ctx.cycle:
        print("next: run MONITOR on %s, save monitor.json, `validate monitor`, then `notify`"
              % ctx.cp("monitor_input.json"))


def _msg(w, typ, price, lvl, idx_move):
    s, z = w["symbol"], w["entry_zone"]
    return {
        "ENTRY_ZONE_REACHED": "%s in entry zone %s–%s (now %s). Stop %s, target %s." % (s, z[0], z[1], r2(price), w["stop"], w["target"]),
        "TARGET_HIT": "%s at target %s (now %s). Plan says take it." % (s, w["target"], r2(price)),
        "STOP_HIT": "%s hit stop %s (now %s). The idea is wrong: get out." % (s, w["stop"], r2(price)),
        "TIME_STOP": "%s: %s bars with no entry. Plan expired (now %s, zone %s–%s)." % (s, lvl, r2(price), z[0], z[1]),
        "MARKET_SHIFT": "Index moved %s%% today. Re-check %s (now %s, stop %s)." % (idx_move, s, r2(price), w["stop"]),
    }[typ][:239]


def record_alerts(ctx, items):
    watch = load(ctx.p("watchlist.json"), [])
    for it in items:
        for w in watch:
            if w["symbol"] == it["symbol"] and w["state"] in ("WAITING", "OPEN"):
                key = {"type": it["type"], "level": it.get("level")}
                if key not in w["alerts_sent"]:
                    w["alerts_sent"].append(key)
                if it.get("thesis_status"):
                    w["thesis_status"] = it["thesis_status"]
    save(ctx.p("watchlist.json"), watch)


def telegram(text):
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        return False
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    try:
        urllib.request.urlopen("https://api.telegram.org/bot%s/sendMessage" % tok, data=data, timeout=15)
        return True
    except Exception as ex:
        print("  ! telegram: %s" % ex)
        return False


def cmd_notify(ctx, a):
    mon = load(ctx.cp("monitor.valid.json")) or die("run `validate monitor` first")
    high = [i for i in mon["items"] if i["priority"] == "HIGH"]
    rest = [i for i in mon["items"] if i["priority"] != "HIGH"]
    if not mon["items"]:
        print("Silence: nothing important happened.")
    for i in high:
        msg = "⚠ %s %s · %s" % (i["symbol"], i["type"], i["message"])
        print("[HIGH] " + msg + ("  (sent)" if telegram(msg) else ""))
    if rest:
        summary = "Summary:\n" + "\n".join("· %s %s: %s" % (i["symbol"], i["type"], i["message"]) for i in rest)
        print(summary)
        if a.batch:
            telegram(summary)


# --------------------------------------------------------- position updates
def _item(ctx, sym, states):
    watch = load(ctx.p("watchlist.json"), [])
    for w in watch:
        if w["symbol"] == sym and w["state"] in states:
            return watch, w
    die("%s is not %s on the watchlist" % (sym, " or ".join(states)))


def cmd_fill(ctx, a):
    watch, w = _item(ctx, a.symbol, ("WAITING",))
    w.update(state="OPEN", entry_price=a.price, opened_at=iso(now_utc()), alerts_sent=[])
    save(ctx.p("watchlist.json"), watch)
    rows = journal_rows(ctx)
    for r in rows:
        if r["id"] == w["journal_id"]:
            r["decision"], r["result_r"] = "Execute", "open"
            r["why"] = (r.get("why") or "") + " | filled from watchlist"
    journal_write(ctx, rows)
    print("%s OPEN at %s, stop %s, target %s" % (a.symbol, a.price, w["stop"], w["target"]))


def cmd_move_stop(ctx, a):
    watch, w = _item(ctx, a.symbol, ("OPEN", "WAITING"))
    long_ = w["direction"] == "LONG"
    if (long_ and a.price < w["stop"]) or (not long_ and a.price > w["stop"]):
        die("rule 11: stops only move toward profit. %s stop is %s; %s would widen it." % (a.symbol, w["stop"], a.price))
    w["stop"] = a.price
    w["alerts_sent"] = [x for x in w["alerts_sent"] if x["type"] != "STOP_HIT"]
    save(ctx.p("watchlist.json"), watch)
    print("%s stop moved to %s" % (a.symbol, a.price))


def cmd_close(ctx, a):
    watch, w = _item(ctx, a.symbol, ("OPEN", "WAITING"))
    t = now_utc()
    if w["state"] == "WAITING":
        w["state"] = "CLOSED"
        save(ctx.p("watchlist.json"), watch)
        print("%s removed from the watchlist (never entered)." % a.symbol)
        return
    sign = 1 if w["direction"] == "LONG" else -1
    pnl = round((a.price - w["entry_price"]) * w["size"] * sign, 2)
    risk = w["size"] * abs(w["entry_price"] - w["initial_stop"])
    rmult = round(pnl / risk, 2) if risk else None
    w.update(state="CLOSED", exit_price=a.price, closed_at=iso(t), pnl=pnl, result_r=rmult)
    save(ctx.p("watchlist.json"), watch)
    rows = journal_rows(ctx)
    for r in rows:
        if r["id"] == w["journal_id"]:
            r.update(result_r=rmult, pnl=pnl, closed_at=iso(t), exit_price=a.price)
    journal_write(ctx, rows)
    print("%s CLOSED at %s: P&L %s = %sR" % (a.symbol, a.price, pnl, rmult))


# -------------------------------------------------------------------- review
def cmd_review(ctx, a):
    rows = journal_rows(ctx)
    if not rows:
        print("Journal is empty.")
        return
    print("%-10s %-10s %-11s %5s %5s %-9s %-7s %s" % ("DATE", "SYMBOL", "SETUP", "SCORE", "R:R", "DECISION", "RESULT", "WHY"))
    for r in rows[-a.last:]:
        print("%-10s %-10s %-11s %5s %5s %-9s %-7s %s" % (r["date"], r["symbol"], r.get("setup") or "-",
              r.get("score") or "-", r.get("rr") or "-", r["decision"],
              r["result_r"] if r.get("result_r") is not None else "n/a", (r.get("why") or "")[:50]))
    closed = [r for r in rows if isinstance(r.get("result_r"), (int, float))]

    def band(sc):
        return "n/a" if sc is None else ("90+" if sc >= 90 else "%d-%d" % (sc // 10 * 10, sc // 10 * 10 + 9))
    for title, key in (("BY SETUP", lambda r: r.get("setup") or "?"), ("BY SCORE BAND", lambda r: band(r.get("score")))):
        groups = {}
        for r in closed:
            groups.setdefault(key(r), []).append(r["result_r"])
        print("\n" + title)
        for k, v in sorted(groups.items()):
            print("  %-12s trades %3d  win %3.0f%%  avg %+.2fR  total %+.2fR"
                  % (k, len(v), 100 * sum(1 for x in v if x > 0) / len(v), sum(v) / len(v), sum(v)))
    first = min(dt.date.fromisoformat(r["date"]) for r in rows)
    weeks = (dt.date.today() - first).days / 7
    blocked_gates = set()
    for f in sorted(os.listdir(ctx.p("cycles"))) if os.path.isdir(ctx.p("cycles")) else []:
        rc = load(ctx.p("cycles", f, "risk_checks.json"))
        for v in (rc or {}).get("items", []):
            blocked_gates |= {g["id"].split("_")[0] for g in v["gates"] if not g["pass"]}
    print("\nGO-LIVE CHECKLIST")
    print("  [%s] 4+ weeks of journalled decisions (%.1f)" % ("x" if weeks >= 4 else " ", weeks))
    print("  [%s] 30+ closed trades (%d)" % ("x" if len(closed) >= 30 else " ", len(closed)))
    need = {"G1", "G2", "G3", "G4", "G5"}
    print("  [%s] every gate has blocked a real plan (seen: %s)" % ("x" if need <= blocked_gates else " ",
                                                                  ",".join(sorted(blocked_gates)) or "none"))
    print("  [ ] alerts arrived on time through a full week, nights included (you check)")
    print("  [ ] start at half your planned size for the first month (set risk_per_trade_pct)")


def cmd_status(ctx, a):
    print("dir %s  cycle %s  equity %s" % (ctx.dir, ctx.cycle, ctx.account.get("equity")))
    for w in load(ctx.p("watchlist.json"), []):
        if w["state"] != "CLOSED":
            print("  %-10s %-7s %-5s zone %s stop %s target %s entry %s last %s thesis %s"
                  % (w["symbol"], w["state"], w["direction"], w["entry_zone"], w["stop"], w["target"],
                     w["entry_price"], w.get("last_price"), w.get("thesis_status", "-")))
    if ctx.cycle:
        files = sorted(os.listdir(ctx.p("cycles", ctx.cycle)))
        print("  cycle files: " + ", ".join(files))


# ---------------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default="trader-247")
    ap.add_argument("--cycle")
    sp = ap.add_subparsers(dest="cmd", required=True)
    i = sp.add_parser("init")
    i.add_argument("--preset", help="market preset from presets/ (e.g. nse)")
    f = sp.add_parser("fetch")
    f.add_argument("--symbols")
    f.add_argument("--range", default="1y")
    f.add_argument("--pause", type=float, default=0.3)
    f.add_argument("--csv", help="folder of SYMBOL.csv files (date,open,high,low,close,volume) instead of Yahoo")
    sp.add_parser("prepare")
    v = sp.add_parser("validate")
    v.add_argument("engine", choices=["scan", "signal", "plan", "risk", "monitor"])
    v.add_argument("file", nargs="?")
    sp.add_parser("signal-input")
    sp.add_parser("plan-input")
    r = sp.add_parser("risk")
    r.add_argument("--now", help="pin the clock (ISO, UTC if no offset)")
    r.add_argument("--no-journal", action="store_true")
    c = sp.add_parser("card")
    c.add_argument("--code-only", action="store_true", help="skip the RISK engine's second opinion")
    c.add_argument("--telegram", action="store_true")
    d = sp.add_parser("decide")
    d.add_argument("symbol")
    d.add_argument("choice")
    d.add_argument("--why", default="")
    d.add_argument("--fill", type=float)
    m = sp.add_parser("monitor")
    m.add_argument("--prices", help='JSON {"SYM": price} instead of Yahoo (e.g. from a broker MCP)')
    m.add_argument("--commit", action="store_true", help="code-only: record the code alerts without Claude")
    n = sp.add_parser("notify")
    n.add_argument("--batch", action="store_true", help="also send the non-HIGH summary to Telegram")
    for name in ("fill", "move-stop", "close"):
        x = sp.add_parser(name)
        x.add_argument("symbol")
        x.add_argument("price", type=float)
    rv = sp.add_parser("review")
    rv.add_argument("--last", type=int, default=30)
    sp.add_parser("status")
    a = ap.parse_args(argv)
    ctx = Ctx(a)
    {"init": cmd_init, "fetch": cmd_fetch, "prepare": cmd_prepare, "validate": cmd_validate,
     "signal-input": cmd_signal_input, "plan-input": cmd_plan_input, "risk": cmd_risk, "card": cmd_card,
     "decide": cmd_decide, "monitor": cmd_monitor, "notify": cmd_notify, "fill": cmd_fill,
     "move-stop": cmd_move_stop, "close": cmd_close, "review": cmd_review, "status": cmd_status}[a.cmd](ctx, a)


if __name__ == "__main__":
    main()
