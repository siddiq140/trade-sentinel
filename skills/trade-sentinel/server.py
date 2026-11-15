#!/usr/bin/env python3
"""trade-sentinel 24/7: a localhost dashboard plus a scheduler that runs the five engines on NSE hours.

    python3 skills/trade-sentinel/server.py                 # http://127.0.0.1:8247
    python3 skills/trade-sentinel/server.py --port 9000 --dir trader-247

Standard library only (plus the optional `anthropic` package for the Claude engines).
Run it from the folder that holds trader-247/. It never places an order.

Schedule (IST, NSE trading days; weekends and NSE holidays are skipped):
  08:30          universe + full refresh of every stock's daily bars
  09:30-15:30    scan cycle every scan_interval_min (15): liquid stocks refreshed, then
                 SCAN -> SIGNAL -> PLAN -> RISK -> decision cards
  09:15-15:35    monitor every monitor_interval_min (5): watchlist prices, alerts, Telegram
  15:45          full refresh and an end-of-day cycle (plans for tomorrow)
With no Claude credentials the code steps still run (data, scan triggers, monitor price
alerts); the judgement steps wait, and the dashboard says so.
"""
import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import threading
import time
import traceback
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(HERE, "engine.py")
CLAUDE = os.path.join(HERE, "claude_engines.py")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

# NSE trading holidays, capital market segment (NSE list for 2026). Add the 2027 list when NSE publishes it,
# or put {"YYYY-MM-DD": "name"} in trader-247/holidays.json.
NSE_HOLIDAYS = {
    "2026-01-15": "Municipal Corporation Election - Maharashtra", "2026-01-26": "Republic Day",
    "2026-02-15": "Mahashivratri", "2026-03-03": "Holi", "2026-03-21": "Id-Ul-Fitr (Ramadan Eid)",
    "2026-03-26": "Shri Ram Navami", "2026-03-31": "Shri Mahavir Jayanti", "2026-04-03": "Good Friday",
    "2026-04-14": "Dr. Baba Saheb Ambedkar Jayanti", "2026-05-01": "Maharashtra Day", "2026-05-28": "Bakri Id",
    "2026-06-26": "Muharram", "2026-08-15": "Independence Day", "2026-09-14": "Ganesh Chaturthi",
    "2026-10-02": "Mahatma Gandhi Jayanti", "2026-10-20": "Dussehra", "2026-11-08": "Diwali Laxmi Pujan",
    "2026-11-10": "Diwali-Balipratipada", "2026-11-24": "Prakash Gurpurb Sri Guru Nanak Dev",
    "2026-12-25": "Christmas",
}

LOG = deque(maxlen=400)
STATE = {"running": None, "jobs": {}, "started": None}
JOB_LOCK = threading.Lock()


def now_ist():
    return dt.datetime.now(IST)


def log(msg):
    line = "%s  %s" % (now_ist().strftime("%d %b %H:%M:%S"), msg)
    LOG.append(line)
    print(line, flush=True)


def load(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


class App:
    def __init__(self, data_dir):
        self.dir = data_dir
        self.holidays = dict(NSE_HOLIDAYS)
        self.holidays.update(load(os.path.join(data_dir, "holidays.json"), {}) or {})

    def p(self, *parts):
        return os.path.join(self.dir, *parts)

    @property
    def config(self):
        return load(self.p("config.json"), {}) or {}

    # ----------------------------------------------------------- calendar
    def trading_day(self, d):
        return d.weekday() < 5 and d.isoformat() not in self.holidays

    def market(self, t=None):
        t = t or now_ist()
        d = t.date()
        o = t.replace(hour=9, minute=15, second=0, microsecond=0)
        c = t.replace(hour=15, minute=30, second=0, microsecond=0)
        if not self.trading_day(d):
            why = self.holidays.get(d.isoformat(), "weekend")
            state, label = "closed", "Closed today (%s)" % why
        elif t < o:
            state, label = "pre", "Opens at 09:15 IST"
        elif t < c:
            state, label = "open", "Open until 15:30 IST"
        else:
            state, label = "closed", "Closed for the day"
        nxt = d if (state == "pre") else d + dt.timedelta(days=1)
        while not self.trading_day(nxt):
            nxt += dt.timedelta(days=1)
        warn = None
        if str(nxt.year) not in {k[:4] for k in self.holidays}:
            warn = "No NSE holiday list for %d yet: weekends are known, holidays are not." % nxt.year
        return {"state": state, "label": label, "next_open": "%s 09:15 IST" % nxt.strftime("%a %d %b"),
                "holiday_warning": warn}

    # --------------------------------------------------------------- runs
    def sh(self, script, *args, timeout=1800):
        cmd = [sys.executable, script, "--dir", self.dir] + list(args) if script == ENGINE else \
              [sys.executable, script] + list(args) + ["--dir", self.dir]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = (r.stdout + r.stderr).strip()
        for line in out.splitlines()[-12:]:
            log("    " + line)
        return r.returncode == 0, out

    def claude_ready(self):
        sys.path.insert(0, HERE)
        try:
            import claude_engines
            return claude_engines.available()
        finally:
            sys.path.pop(0)

    def job_refresh(self):
        log("refresh: NSE equity list and every stock's daily bars")
        self.sh(ENGINE, "universe")
        self.sh(ENGINE, "fetch", timeout=3600)

    def job_cycle(self, quick=True):
        log("cycle: refreshing %s stocks" % ("liquid" if quick else "all"))
        self.sh(ENGINE, "fetch", *(["--liquid-only"] if quick else []), timeout=3600)
        ok, _ = self.sh(ENGINE, "prepare")
        if not ok:
            return
        ready, why = self.claude_ready()
        if not ready:
            log("cycle: scan input ready; Claude steps skipped: %s" % why)
            return
        steps = [("scan", None), ("signal", "signal-input"), ("plan", "plan-input"), ("risk", "risk")]
        for engine, prep in steps:
            if prep:
                ok, _ = self.sh(ENGINE, prep)
                if not ok:
                    return
            if engine == "risk":
                checks = load(self.cp("risk_checks.json"), {}) or {}
                if not any(v["verdict"] == "APPROVED" for v in checks.get("items", [])):
                    log("cycle: no plan passed the code gates; nothing for the RISK engine")
                    return
            if engine in ("signal", "plan"):
                prev = load(self.cp("scan.valid.json" if engine == "signal" else "signal.valid.json"), {}) or {}
                items = prev.get("items", [])
                if engine == "plan":
                    items = [i for i in items if i.get("status") == "PASS"]
                if not items:
                    log("cycle: nothing passed to %s; cycle ends here" % engine.upper())
                    return
            ok, _ = self.sh(CLAUDE, engine, timeout=1200)
            if not ok:
                log("cycle: %s engine failed; cycle stops" % engine.upper())
                return
            ok, _ = self.sh(ENGINE, "validate", engine)
            if not ok:
                return
        self.sh(ENGINE, "card")
        cards = load(self.cp("cards.json"), []) or []
        if cards:
            log("cycle: %d decision card(s) waiting for you" % len(cards))
            self.telegram_cards(cards)

    def job_monitor(self):
        watch = load(self.p("watchlist.json"), []) or []
        if not any(w.get("state") in ("WAITING", "OPEN") for w in watch):
            return
        if not os.path.exists(self.p("current_cycle")):
            return
        ok, _ = self.sh(ENGINE, "monitor")
        if not ok:
            return
        inp = load(self.cp("monitor_input.json"), {}) or {}
        news = any(w.get("headlines") for w in inp.get("watchlist", []))
        ready, _ = self.claude_ready()
        if ready and (inp.get("code_alerts") or news):
            ok, _ = self.sh(CLAUDE, "monitor", timeout=600)
            if ok:
                ok, _ = self.sh(ENGINE, "validate", "monitor")
        else:
            ok, _ = self.sh(ENGINE, "monitor", "--commit") if inp.get("code_alerts") else (False, "")
        if ok:
            mon = load(self.cp("monitor.valid.json"), {}) or {}
            with open(self.p("alerts.jsonl"), "a") as f:
                for it in mon.get("items", []):
                    f.write(json.dumps(dict(it, at=now_ist().isoformat(timespec="seconds"))) + "\n")
            self.sh(ENGINE, "notify")

    def telegram_cards(self, cards):
        if os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"):
            self.sh(ENGINE, "card", "--telegram")

    def cp(self, name):
        cyc = (open(self.p("current_cycle")).read().strip() if os.path.exists(self.p("current_cycle")) else "")
        return self.p("cycles", cyc, name)

    def run(self, name, fn, *args):
        if not JOB_LOCK.acquire(blocking=False):
            log("%s: skipped, %s is still running" % (name, STATE["running"]))
            return False
        STATE["running"] = name
        job = STATE["jobs"].setdefault(name, {})
        job.update(last_start=now_ist().isoformat(timespec="seconds"), status="running")
        try:
            fn(*args)
            job["status"] = "ok"
        except Exception as ex:
            job["status"] = "error: %s" % ex
            log("%s failed: %s" % (name, ex))
            traceback.print_exc()
        finally:
            job["last_end"] = now_ist().isoformat(timespec="seconds")
            STATE["running"] = None
            JOB_LOCK.release()
        return True

    def run_async(self, name, fn, *args):
        threading.Thread(target=self.run, args=(name, fn) + args, daemon=True).start()

    # ---------------------------------------------------------- scheduler
    def due(self, name, every_min, t):
        last = STATE["jobs"].get(name, {}).get("last_start")
        if not last:
            return True
        return (t - dt.datetime.fromisoformat(last)).total_seconds() >= every_min * 60 - 5

    def once_today(self, name, t):
        last = STATE["jobs"].get(name, {}).get("last_start")
        return not last or dt.datetime.fromisoformat(last).date() != t.date()

    def scheduler(self):
        log("scheduler started (IST). %s" % self.market()["label"])
        bars = self.p("bars")
        if not os.path.isdir(bars) or len(os.listdir(bars)) < 100:
            self.run("refresh", self.job_refresh)
        while True:
            try:
                t = now_ist()
                cfg = self.config
                hm = t.strftime("%H:%M")
                if self.trading_day(t.date()):
                    if "08:30" <= hm < "09:10" and self.once_today("refresh", t):
                        self.run("refresh", self.job_refresh)
                    elif "09:30" <= hm <= "15:30" and self.due("cycle", cfg.get("scan_interval_min", 15), t):
                        self.run("cycle", self.job_cycle, True)
                    elif "09:15" <= hm <= "15:35" and self.due("monitor", cfg.get("monitor_interval_min", 5), t):
                        self.run("monitor", self.job_monitor)
                    elif hm >= "15:45" and self.once_today("eod", t):
                        self.run("eod", self.job_cycle, False)
            except Exception as ex:
                log("scheduler error: %s" % ex)
            time.sleep(20)

    # -------------------------------------------------------------- state
    def state(self):
        cyc = open(self.p("current_cycle")).read().strip() if os.path.exists(self.p("current_cycle")) else None
        c = (lambda n: load(self.p("cycles", cyc, n))) if cyc else (lambda n: None)
        scan_in = c("scan_input.json") or {}
        sig = (c("signal.valid.json") or {}).get("items", [])
        plans = (c("plan.valid.json") or {}).get("items", [])
        checks = (c("risk_checks.json") or {}).get("items", [])
        cards = c("cards.json") or []
        journal = []
        if os.path.exists(self.p("journal.jsonl")):
            journal = [json.loads(l) for l in open(self.p("journal.jsonl")) if l.strip()]
        decided = {r["id"] for r in journal if r.get("decision") in ("Execute", "Watch", "Pass")}
        pending = []
        for card in cards:
            if "%s:%s" % (cyc, card["symbol"]) in decided:
                continue
            plan = next((p for p in plans if p["symbol"] == card["symbol"]), None)
            verdict = next((v for v in checks if v["symbol"] == card["symbol"]), None)
            s = next((x for x in sig if x["symbol"] == card["symbol"]), {})
            pending.append({"symbol": card["symbol"], "card": card["card"], "plan": plan, "verdict": verdict,
                            "setup": s.get("setup"), "score": s.get("score"), "cycle": cyc})
        alerts = []
        if os.path.exists(self.p("alerts.jsonl")):
            alerts = [json.loads(l) for l in open(self.p("alerts.jsonl")) if l.strip()][-60:][::-1]
        ready, why = self.claude_ready()
        uni = load(self.p("universe.json"), []) or []
        steps = {n: os.path.exists(self.p("cycles", cyc, n)) for n in
                 ("scan.valid.json", "signal.valid.json", "plan.valid.json", "risk_checks.json",
                  "risk.valid.json", "cards.json")} if cyc else {}
        return {
            "now": now_ist().strftime("%a %d %b %Y, %H:%M IST"),
            "market": self.market(),
            "claude": {"ready": ready, "why": why, "model": self.config.get("claude_model")},
            "telegram": bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID")),
            "running": STATE["running"], "jobs": STATE["jobs"],
            "account": load(self.p("account.json"), {}),
            "config": {k: self.config.get(k) for k in ("min_liquidity", "pass_mark", "scan_interval_min",
                                                        "monitor_interval_min", "max_shortlist")},
            "cycle": {"id": cyc, "universe": len(uni), "liquid": None if not cyc else len(c("metrics.json") or {}),
                      "shortlist": len(scan_in.get("universe", [])),
                      "shortlist_items": [{k: m.get(k) for k in ("symbol", "last", "chg_1d_pct", "volume_ratio",
                                                                 "code_triggers", "intraday_partial")}
                                          for m in scan_in.get("universe", [])[:60]],
                      "candidates": len((c("scan.valid.json") or {}).get("items", [])),
                      "signals": sig, "plans": plans, "checks": checks, "steps": steps},
            "pending": pending,
            "watchlist": [w for w in (load(self.p("watchlist.json"), []) or []) if w.get("state") != "CLOSED"],
            "alerts": alerts,
            "journal": journal[-40:][::-1],
            "log": list(LOG)[-120:],
        }


def make_handler(app):
    page = os.path.join(HERE, "dashboard.html")

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                return self.send(200, open(page, "rb").read(), "text/html; charset=utf-8")
            if path == "/api/state":
                return self.send(200, app.state())
            if path == "/api/bars":
                from urllib.parse import parse_qs, urlparse
                sym = parse_qs(urlparse(self.path).query).get("symbol", [""])[0]
                safe = "".join(ch if ch.isalnum() or ch in "-_.&" else "_" for ch in sym).replace("&", "_")
                bars = load(app.p("bars", safe + ".json"), []) or []
                return self.send(200, bars[-90:])
            self.send(404, {"error": "not found"})

        def do_POST(self):
            # A custom header blocks plain cross-site form posts from other web pages.
            if self.headers.get("X-Trade-Sentinel") != "1":
                return self.send(403, {"error": "missing X-Trade-Sentinel header"})
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self.send(400, {"error": "body is not JSON"})
            path = self.path.split("?")[0]
            if path == "/api/run":
                job = body.get("job")
                fns = {"cycle": (app.job_cycle, True), "eod": (app.job_cycle, False),
                       "monitor": (app.job_monitor,), "refresh": (app.job_refresh,)}
                if job not in fns:
                    return self.send(400, {"error": "job must be one of " + ", ".join(fns)})
                if STATE["running"]:
                    return self.send(409, {"error": "%s is running; try again when it finishes" % STATE["running"]})
                app.run_async(job, *fns[job])
                return self.send(202, {"ok": True, "message": "%s started" % job})
            if path == "/api/decide":
                args = ["--cycle", body.get("cycle") or "", "decide", body.get("symbol", ""), body.get("choice", ""),
                        "--why", body.get("why") or ""]
                if body.get("fill") not in (None, ""):
                    args += ["--fill", str(body["fill"])]
                ok, out = app.sh(ENGINE, *args)
                return self.send(200 if ok else 400, {"ok": ok, "message": out.replace("ERROR: ", "")})
            if path == "/api/position":
                action = body.get("action")
                if action not in ("fill", "move-stop", "close"):
                    return self.send(400, {"error": "action must be fill, move-stop or close"})
                ok, out = app.sh(ENGINE, action, body.get("symbol", ""), str(body.get("price", "")))
                return self.send(200 if ok else 400, {"ok": ok, "message": out.replace("ERROR: ", "")})
            self.send(404, {"error": "not found"})

    return H


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default="trader-247")
    ap.add_argument("--host", default="127.0.0.1", help="keep 127.0.0.1 unless you know why not")
    ap.add_argument("--port", type=int, default=8247)
    ap.add_argument("--no-scheduler", action="store_true", help="dashboard only")
    a = ap.parse_args()
    if not os.path.exists(os.path.join(a.dir, "config.json")):
        sys.exit("No %s/config.json. Run `python3 %s init --preset nse` first." % (a.dir, ENGINE))
    app = App(a.dir)
    STATE["started"] = now_ist().isoformat(timespec="seconds")
    if not a.no_scheduler:
        threading.Thread(target=app.scheduler, daemon=True).start()
    srv = ThreadingHTTPServer((a.host, a.port), make_handler(app))
    log("dashboard on http://%s:%d  (Ctrl+C to stop)" % (a.host, a.port))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log("stopped")


if __name__ == "__main__":
    main()
