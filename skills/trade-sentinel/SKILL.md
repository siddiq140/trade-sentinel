---
name: trade-sentinel
description: >-
  The 24/7 AI Trading System (AFTER2AM build guide v1.0). A five-engine
  research and alerting pipeline for any market (US stocks, NSE, crypto):
  SCAN flags unusual assets, SIGNAL classifies each into one of five setups
  (BREAKOUT, PULLBACK, MOMENTUM, TREND_CONT, REVERSAL) and scores it 0-100
  (70 passes), PLAN writes entry zone, stop, target, invalidation, time stop
  and reward:risk, RISK runs twelve rules as gates G1-G5 with BLOCKED as the
  default, and MONITOR watches approved plans and open trades and alerts
  once per event. Every engine hands off strict JSON in one envelope,
  validated in code before the next engine sees it. Code does all the
  maths (indicators, triggers, position size, exposure, R:R, the risk
  verdict); Claude plays each engine from its copy-paste prompt; a human
  answers a decision card with EXECUTE, WATCH or PASS and places every
  order. Writes a journal in R and a go-live checklist. Use when the user
  says "run trade sentinel", "run the trading system", "run a cycle", "scan, signal, plan",
  "five engines", "24/7 trading system", "decision card", "run the
  monitor", "check my watchlist alerts", "journal review", "build the
  AFTER2AM system", or "set up my risk rules".
argument-hint: "[init | cycle | monitor | review]"
---

# trade-sentinel

The build behind "The 24/7 AI Trading System": **don't build one giant
prompt, build a system.** Five small engines, each with one job and a strict
output format. Each engine's output is the next engine's input.

```
MARKET DATA (code)  bars + headlines
  01 SCAN     -> candidate[]        fast model     every 15 min in market hours
  02 SIGNAL   -> signal[]           strongest      score >= 70 = PASS
  03 PLAN     -> plan[]             strongest      for every PASS signal
  04 RISK     -> risk_verdict[]     code decides   default answer BLOCKED
  05 MONITOR  -> alert[]            fast model     every 5-15 min, 24/7 for crypto
  YOU         execute · watch · pass  (you place every order)
```

Two rules hold everywhere:

1. **Code does the maths.** Indicators, position size, exposure and R:R come
   from `engine.py`. Claude reads them, judges structure and explains. Never
   compute a size, an R:R or a verdict in your head.
2. **A human places the order.** This skill prepares, filters and protects.
   It never sends an order to any broker. (If the user later wants an order
   sent, hand off to `trader-broker` or `india-broker`, which ask for an
   explicit yes; India regulates algorithmic orders from retail accounts.)

`${CLAUDE_SKILL_DIR}` is this skill's folder. Work happens in `trader-247/`
in the current directory (`--dir` to change it).

```bash
E="python3 ${CLAUDE_SKILL_DIR}/engine.py"
$E init                                   # trader-247/ with config, risk rules, account, universe
$E fetch                                  # 1y daily bars, free Yahoo chart JSON (or --csv DIR)
$E prepare                                # new cycle: indicators + code triggers -> scan_input.json
$E validate scan                          # after you write scan.json as the SCAN engine
$E signal-input   ; $E validate signal
$E plan-input     ; $E validate plan      # code recomputes rr; > 0.05 off = item dropped
$E risk           ; $E validate risk      # code gates first, your RISK opinion second
$E card                                   # decision cards: only where BOTH said APPROVED
$E decide NOVA EXECUTE --fill 118.52 --why "clean level, volume confirmed"
$E monitor        ; $E validate monitor ; $E notify
$E fill NOVA 118.55 | move-stop NOVA 116 | close NOVA 128.10
$E review | status
```

## Setup (first time)

1. `$E init` (or `$E init --preset nse` for NSE: NIFTY 50 universe, `^NSEI`, ₹1,00,000
   equity, ₹50 crore liquidity floor, `allow_short` false), then edit with the user:
   - `trader-247/universe.json`: one market, 50-300 liquid symbols, each
     `{"symbol", "sector"}`. Yahoo symbols: `AAPL`, `RELIANCE.NS`, `BTC-USD`.
   - `trader-247/account.json`: `equity` (and `currency_symbol` for cards).
   - `trader-247/risk_rules.json`: the twelve rules below. Defaults are
     conservative. Tighten freely; loosen one only after 4+ weeks of journal
     data shows why.
   - `trader-247/config.json`: `index_symbol` (`^GSPC`, `^NSEI`, `BTC-USD`),
     `min_liquidity` (avg daily traded value, in the market's currency),
     `max_candidates` 40, `pass_mark` 70, `market_shock_pct` 2.0, `allow_short`
     (false where overnight shorts are impossible, e.g. NSE cash: gate G0_SHORT blocks them).
2. `$E fetch`. One bad symbol never stops the run. Offline or with a vendor:
   `--csv DIR` with `SYMBOL.csv` (date,open,high,low,close,volume).
3. Optional inputs Claude fills before a cycle:
   - `trader-247/headlines.json`: `{"SYM": [{"title", "time", "source"}]}`
     from the last 24 h. Use `trader-news` / `india-news` or a web search.
     Never invent a headline.
   - `trader-247/events.json`: `{"SYM": ["2026-11-20T21:00:00Z"]}` for
     earnings and scheduled announcements (rule 10, event blackout).

## Running one cycle

Run each step, read its output, then act as the next engine. **Before acting
as an engine, Read its prompt file in `prompts/` and follow it exactly**,
substituting the `{{...}}` values given in the input file. Write **JSON only**
in the envelope:

```json
{ "cycle_id": "<from the input file>", "engine": "plan", "version": "1.0",
  "generated_at": "<UTC ISO time>", "items": [ ... ], "warnings": [] }
```

| step | command | you (Claude) do | file you write |
| --- | --- | --- | --- |
| data | `fetch`, `prepare` | fill headlines/events first if you have them | |
| 01 | read `scan_input.json` | SCAN per `prompts/scan.md`: code already fired triggers 1, 2, 4; you add headline triggers (rule 3) and rank | `cycles/<id>/scan.json` |
| | `validate scan`, `signal-input` | | |
| 02 | read `signal_input.json` | SIGNAL per `prompts/signal.md`: setup, score, levels from the bars, evidence citing bar dates | `signal.json` |
| | `validate signal`, `plan-input` | | |
| 03 | read `plan_input.json` | PLAN per `prompts/plan.md`, using the given `atr14` | `plan.json` |
| | `validate plan`, `risk` | | |
| 04 | read `risk_input.json` | RISK per `prompts/risk.md`: a second opinion on code's `checks[]`; check consistency, explain | `risk.json` |
| | `validate risk`, `card` | show the user each card verbatim | |
| you | user answers | record it: `decide SYM EXECUTE/WATCH/PASS --why "..."` | |

If a step prints `0 valid` or nothing passes, the cycle ends there: say so.
"Nothing worth trading" is a normal, good result. Validation drops any item
that does not parse, misses a field, has a stop on the wrong side, an R:R that
differs from code's by more than 0.05, a signal level that is not a price in
the last 60 bars, or a status that disagrees with the score. A dropped item
stops for that item only; never patch it by hand to get it through. Re-run
the engine instead.

**The verdict that counts is code's** (`risk_checks.json`). `card` shows a
plan only when code AND the RISK engine both say APPROVED. If code says
BLOCKED, the trade is blocked, whatever the model says. Blocked plans go into
the journal automatically with the reason.

### The decision card and the three answers

```
▲ NOVA · BREAKOUT · LONG        score 82
entry   118.4 – 118.6
stop    114.2      target  128.4
R:R     1 : 2.30   size    58 units
risk    249.4 (1.0%)   gates  5 / 5 pass
thesis  Range breakout on volume.
wrong if daily close back below 118.40 before entry
reply:  EXECUTE · WATCH · PASS
```

- **EXECUTE**: the user places the order with their broker, exactly as
  planned. `decide ... EXECUTE --fill <their fill>`: the item becomes OPEN.
- **WATCH**: no position; the monitor watches it (WAITING) and alerts if it
  gets better or breaks. If they enter later: `fill SYM PRICE`.
- **PASS**: archived with its score. No trade is also a position.

Ask for one line of why (twenty words is enough). The journal is where the
real edge comes from.

## The monitor (engine 05)

`$E monitor` fetches the latest price for every WAITING/OPEN item (or takes
`--prices file.json`, e.g. live quotes from a broker MCP such as Fyers'
`get_quotes`) and fires the price alerts in code: ENTRY_ZONE_REACHED,
TARGET_HIT, STOP_HIT (HIGH), TIME_STOP, MARKET_SHIFT. A level re-arms only
after the price leaves it, so each crossing alerts once. Then act as MONITOR
per `prompts/monitor.md` on `monitor_input.json`: keep the code alerts, add
INVALIDATED and NEWS from headlines, set `thesis_status`
(VALID/WEAKENING/BROKEN) from `change_my_mind`. Write `monitor.json`,
`validate monitor` (records `alerts_sent`), then `notify`. Silence (empty
items) is a valid answer.

`notify` sends HIGH alerts to Telegram when `TELEGRAM_BOT_TOKEN` and
`TELEGRAM_CHAT_ID` are set in the environment (never ask for the token in
chat), and prints the rest as one summary (`--batch` to send it too). If
every alert buzzes, the user stops reading them by day three.

Without Claude in the loop (a plain cron): `monitor --commit` records and
passes the code alerts as-is.

Stops only move one way (rule 11): `move-stop` refuses to widen one.
`close SYM PRICE` writes the result in R (P&L / initial risk) to the journal.

## Running it 24/7

**Localhost server (NSE, recommended):** `./start.sh` (Windows `start.bat`) or
`python3 ${CLAUDE_SKILL_DIR}/server.py`, then http://127.0.0.1:8247. It downloads every NSE
equity (`engine.py universe`: EQUITY_L.csv, series EQ, ~2,400), refreshes bars 6 at a time
(~2-3 min for all, ~1 min for the liquid ones), and runs on IST trading days: 08:30 full
refresh, 09:30-15:30 a cycle every `scan_interval_min`, 09:15-15:35 the monitor every
`monitor_interval_min`, 15:45 an end-of-day cycle. With `anthropic` installed and credentials
set, `claude_engines.py` runs SCAN/SIGNAL/PLAN/RISK/MONITOR through the Claude API
(`claude_model`, per-engine `claude_effort`, server-side refusal fallback on); each reply still
goes through `engine.py validate`. Without credentials the code steps run and the cycle waits
at SCAN: when the user asks, act as the engines on the current cycle exactly as in "Running
one cycle" and the dashboard picks the files up. Decisions, fills, stop moves and closes can be
made in the browser. The server binds to 127.0.0.1 and refuses POSTs without its own header.
During market hours a stock's last bar is still forming: `volume_ratio` is projected from the
share of the session elapsed (`intraday_partial`, raw value in `volume_ratio_raw`).

Other ways:

The stack is deliberately boring: a scheduler, a data source, Claude, a
JSON store, a phone alert. Scan every 15 min in market hours; monitor every 5
(crypto runs 24/7). Options, simplest first:

- In this session: `/loop 15m run one trade-sentinel cycle` and
  `/loop 5m run the trade-sentinel monitor`.
- A Claude Code scheduled task (create_trigger) that runs a fresh session
  per firing with the same instruction.
- Cron on a small server with `claude -p "run one trade-sentinel cycle"`.

Cost control: only SIGNAL and PLAN need the strongest model, and only on the
shortlist. SCAN and MONITOR run fine on a fast model, and code does all the
arithmetic. Most cycles should cost cents.

## The twelve risk rules (`risk_rules.json`)

| # | rule | default | gate | why |
| --- | --- | --- | --- | --- |
| 01 | risk per trade | 1% (hard cap `max_risk_pct` 2%) | G1 | ten losses in a row cost ~10%, not the account |
| 02 | minimum reward:risk | 2.0 | G3 | at 2:1 you can be wrong more often than right |
| 03 | max open risk | 5% | G2 | sum of losses at every open stop |
| 04 | max position value | 35% | G2 | a gap through one stop can't sink you |
| 05 | max open positions | 4 | G4 | more than you can watch is more than you can manage |
| 06 | max per sector | 2 | G5 | four trades in one sector are one big trade |
| 07 | daily loss limit | 3% | G4 | no new entries until tomorrow |
| 08 | weekly loss limit | 6% | G4 | pause until the journal is reviewed |
| 09 | volatility filter | ATR ≤ 2.0× its 20-day avg | G5 | stops get hit by noise |
| 10 | event blackout | 24 h | G5 | no new entries before earnings / big announcements |
| 11 | stops only move one way | always | `move-stop` | never widen a stop, never average down |
| 12 | no leverage | 1.0× | G2 | borrowed money only multiplies mistakes |

Size: `int(equity × risk% / 100 // |entry_mid − stop|)`: 25,000 × 1% = 250 /
4.30 → 58. Plans in one batch are checked in order as if each approved one
was taken. G0_SHORT blocks a SHORT plan when `allow_short` is false. G0_DATA also blocks a symbol already WAITING/OPEN or a broken plan.
Daily/weekly P&L come from closed journal rows.

## Journal and review

`trader-247/journal.jsonl`: one row per decision (date, symbol, setup, score,
R:R, decision Execute / Watch / Pass / Blocked, why, result in R). `$E review`
prints it, groups closed trades by setup and by score band (after 30 trades,
raise the pass mark for setups that lose: that is how the system learns), and
the go-live checklist:

- 4+ weeks of paper trading with every decision journalled
- at least 30 closed paper trades, reviewed by setup and score
- every risk gate has blocked at least one real plan
- alerts arrived on time through a full week, nights included
- start at half the planned size for the first month

## The 7-day build (if the user is building it from scratch)

D1 universe and data · D2 scan (check 10 outputs by hand against the charts)
· D3 signal + plan (label 20 historical charts by hand first; re-run that
folder after every prompt edit and revert if accuracy drops) · D4 risk (test
with plans built to fail each gate) · D5 monitor + alerts · D6 decision card +
journal · D7 dry run, then 4 weeks of paper trading.

## Files

| file | what |
| --- | --- |
| `engine.py` | the code half: data, indicators, triggers, validation, risk gates, cards, journal, monitor |
| `prompts/{scan,signal,plan,risk,monitor}.md` | the five engine system prompts, verbatim from the guide |
| `risk_rules.example.json`, `config.example.json`, `account.example.json`, `universe.example.json` | copied by `init` |
| `trader-247/cycles/<cycle_id>/` | every input, raw engine output and validated hand-off of one cycle |
| `trader-247/watchlist.json` | WAITING → OPEN → CLOSED state machine |

## What this system is not

Not a promise of profit: it filters and organises; markets still decide. Not
an auto-trader: a human places every order. Not a data source: Claude only
knows what the code sends it, so never invent prices, news or levels. Not
set-and-forget: review the journal every week. Educational, not investment
advice; trading carries a real risk of losing money. Say this once when the
user first runs a cycle, not on every message.
