# trade-sentinel

A Claude Code skill: a five-engine, 24/7 AI trading **research and alerting** system,
based on the AFTER2AM "24/7 AI Trading System" build guide (v1.0).

```
MARKET DATA (code) -> 01 SCAN -> 02 SIGNAL -> 03 PLAN -> 04 RISK -> 05 MONITOR -> YOU (execute · watch · pass)
```

- **Code does the maths**: indicators, triggers, position size, exposure, R:R and the risk verdict (`engine.py`, Python 3.9+, standard library only).
- **Claude plays each engine** from its prompt in `skills/trade-sentinel/prompts/`, handing off strict JSON validated in code.
- **Twelve risk rules** as gates G1-G5; the default answer is BLOCKED.
- **A human places every order.** Nothing here talks to a broker.
- Decision cards, a WAITING → OPEN → CLOSED monitor, Telegram alerts (optional), a journal in R and a go-live checklist.

## Run it 24/7 on your computer (NSE)

```bash
git clone https://github.com/siddiq140/trade-sentinel && cd trade-sentinel
pip install anthropic                 # optional: lets it run the Claude engines unattended
export ANTHROPIC_API_KEY=...          # or `ant auth login`; Windows: set ANTHROPIC_API_KEY=...
./start.sh                            # Windows: start.bat
```

Open **http://127.0.0.1:8247**. The first start downloads every NSE equity (about 2,400, series EQ)
and a year of daily bars (about 2-3 minutes). Then, on NSE trading days (IST, weekends and NSE
holidays skipped):

| time (IST) | job |
| --- | --- |
| 08:30 | refresh the NSE list and every stock's bars |
| 09:30-15:30, every 15 min | refresh liquid stocks (≥ ₹10 crore a day), scan, SIGNAL, PLAN, RISK, decision cards |
| 09:15-15:35, every 5 min | watchlist and open trades: entry zone, stop, target, time stop, market shock alerts |
| 15:45 | full refresh and an end-of-day cycle (plans for the next session) |

Answer decision cards in the browser (Execute with your fill price, Watch, Pass), and move stops or
close trades there. Without Claude credentials the data, scan and price alerts still run; the
judgement steps wait. Telegram: set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` before starting.
It stays up only while your computer is on and the window is open: keep it running with
`nohup ./start.sh &`, a systemd service, or Windows Task Scheduler ("At log on").

**Cost:** each 15-minute cycle sends up to ~25 charts to Claude. On Opus 5.5 that is roughly
₹10-40 per cycle depending on how many stocks reach SIGNAL and PLAN. Lower it in
`trader-247/config.json`: `scan_interval_min` (e.g. 30 or 60), `max_candidates`, or
`claude_model`/`claude_effort` per engine.

## Install

Clone this repo and open it in Claude Code: `.claude/skills` links to `skills/`, so the
`/trade-sentinel` skill loads automatically. Or copy `skills/trade-sentinel/` into
`~/.claude/skills/` to use it in every project.

## Quick start

```bash
E="python3 skills/trade-sentinel/engine.py"
$E init --preset nse   # or plain `init`; creates trader-247/ with config, risk rules, account, universe
$E fetch       # 1y daily bars (free Yahoo data) or --csv DIR
$E prepare     # start a cycle
```

Then ask Claude: "run one trade-sentinel cycle". See `skills/trade-sentinel/SKILL.md` for the full workflow.

Educational, not investment advice. Trading carries a real risk of losing money.
