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
