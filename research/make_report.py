#!/usr/bin/env python3
"""Write research/README.md from the newest research/results/backtest-*.json."""
import glob, json, os
HERE = os.path.dirname(os.path.abspath(__file__))
src = sorted(glob.glob(os.path.join(HERE, "results", "backtest-*.json")))[-1]
d = json.load(open(src)); m = d["meta"]; R = d["results"]
stamp = os.path.basename(src)[9:-5]
f = lambda v, p=2: "–" if v is None else ("%+.*f" % (p, v))
L = ["# 5-year test of the swing rules on NSE", "",
     "Data **%s to %s**, %s NSE equities with enough history, liquidity at least ₹10 crore a day at the time of each signal. "
     "Nifty 50 over the period: **%+.1f%%** (Nov 2021 – Nov 2024: %+.1f%%, Nov 2024 – Nov 2026: %+.1f%%)." % (
         m["data_from"], m["data_to"], f"{m['stocks']:,}", m["nifty_change_pct"], m["nifty_in_sample_pct"], m["nifty_out_of_sample_pct"]), "",
     "**R** is profit or loss divided by the amount risked on the trade. +0.10R a trade means risking ₹1,000 a trade "
     "earned ₹100 on average. Costs of %.1f%% a round trip are included unless a column says otherwise." % m["cost_round_trip_pct"], "",
     "## Verdict", ""]
best = max(R, key=lambda r: r["all"]["exp_r"])
pos_oos = [r["code"] for r in R if (r["out_of_sample"].get("exp_r") or -1) > 0]
L += ["- **No tested rule set made money after costs over the 5 years.** The best, %s, averaged %sR a trade." % (best["code"], f(best["all"]["exp_r"], 3)),
      "- **Held-out period (Nov 2024 – Nov 2026): %s.**" % ("every variant lost money" if not pos_oos else "positive only for " + ", ".join(pos_oos)),
      "- **Breakouts lost in every single year**, including the strong market of 2023. The original tight-stop version (B1) is clearly negative (t = %s)." % R[0]["all"]["t_stat"],
      "- **Pullbacks worked in 2022–2023 and stopped working from 2024.** Before costs they were slightly positive (%sR); costs turn that into a loss." % f(next(r for r in R if r["code"] == "P2")["all"]["exp_r_before_costs"], 3),
      "- **Do not trade these rules with real money.** Keep the daily runs as paper trades and treat every card as untested.", "",
      "## Results", "",
      "| Code | Rules | Trades | Win % | Avg R after costs | Before costs | t-stat | Nov 21–Nov 24 | Nov 24–Nov 26 | 4-at-a-time total | Worst drawdown |",
      "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
for r in R:
    a, i, o, p = r["all"], r["in_sample"], r["out_of_sample"], r["portfolio_4_open"]
    L.append("| **%s** | %s | %s | %.1f | **%s** | %s | %s | %s | %s | %sR | %sR |" % (
        r["code"], r["rules"], f"{a['trades']:,}", a["win_pct"], f(a["exp_r"], 3), f(a["exp_r_before_costs"], 3), a["t_stat"],
        f(i.get("exp_r"), 3), f(o.get("exp_r"), 3), f(p["total_r"], 1), f(p["worst_drawdown_r"], 1)))
L += ["", "A t-stat below −2 means the loss is very unlikely to be bad luck; between −2 and +2 means no clear edge either way.", "",
      "### Average R per trade by year (after costs)", "", "| Code | " + " | ".join(sorted(R[0]["by_year"])) + " |",
      "| --- | " + " | ".join("---:" for _ in R[0]["by_year"]) + " |"]
for r in R:
    L.append("| %s | " % r["code"] + " | ".join("%s (%s)" % (f(r["by_year"][y]["exp_r"], 2), f"{r['by_year'][y]['trades']:,}") for y in sorted(r["by_year"])) + " |")
L += ["", "(trades in brackets; 2022 starts in mid-February because each signal needs 60 days of history)", "",
      "## Cumulative result, 1% risk a trade, at most 4 open", "", "![Cumulative R](results/equity-%s.svg)" % stamp, "",
      "## How the test works", "",
      "- **Signals** follow the trade-sentinel definitions: a BREAKOUT is a close above a level touched 2+ times in the prior 20 sessions "
      "on at least 1.5× average volume; a PULLBACK is a stock above a rising 50-day average that dips to its 20-day average, makes a higher low and closes up.",
      "- **Plans** follow the PLAN engine and the swing rules: entry zone from the setup, structural stop, target the nearer of the prior swing high and entry + 3 ATR, "
      "reward:risk at least 2, entry zone within 1 ATR of the signal close.",
      "- **Trades** enter within 3 sessions (or at the next open where stated) and exit at the stop, the target, or the close of the 10th session. "
      "When the stop and target are both inside one day's range the stop is assumed first.",
      "- The rules were fixed before the results were seen, and the Nov 2024 – Nov 2026 period was not used to choose them.", "",
      "## Limits", "",
      "- Only stocks listed today are included, so companies that delisted or went bust are missing; real results would likely be a little worse.",
      "- Yahoo daily prices, split-adjusted; no slippage beyond the cost estimate; circuit-locked days can make real stops fill worse.",
      "- Claude's chart judgment (the SIGNAL score and the RISK second opinion) is not tested here. It has no track record yet; "
      "the daily runs' journal is the way to measure it.",
      "- Results are per signal; the 4-at-a-time column shows one realistic way of taking them, not the only one.", "",
      "## Re-run", "", "```bash", "python3 research/fetch_history.py          # ~4 min, 5 years for every NSE equity",
      "python3 research/backtest.py               # ~20 s", "python3 research/make_report.py            # this page", "```", "",
      "Data: [results/backtest-%s.json](results/backtest-%s.json)." % (stamp, stamp), "",
      "_Educational, not investment advice._", ""]
open(os.path.join(HERE, "README.md"), "w").write("\n".join(L))
print("wrote research/README.md from", os.path.basename(src))
