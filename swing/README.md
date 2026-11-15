# Trade Sentinel Swing · 1-10 day trades (NSE)

The same five engines and all 12 risk rules as the main run, tuned for trades you hold
**1 to 10 trading days**. **Latest run as pictures: [snapshots/](snapshots/README.md)**.

## Swing rules (on top of the 12 risk rules)

| rule | setting | why |
| --- | --- | --- |
| Entry near today's price | entry zone within **1 ATR** of the last close (gate G6_ENTRY_NEAR) | no waiting weeks for a deep pullback |
| Wait for entry | at most **3 sessions**, then the plan expires | a swing that does not trigger soon is a different trade |
| Reachable target | the nearer of the next resistance and entry + **3 ATR** (gate G6_TARGET_REACH) | about what a stock travels in 10 days |
| Max hold | **10 trading days** after entry (gate G6_HOLD); the monitor alerts "exit at the close" on day 10 | your holding limit |
| Reward : risk | still at least **2 : 1** | a closer target must not mean a worse trade |
| Everything else | 1% risk a trade, 35% max position, 5% open risk, 4 positions, 2 per sector, 3% daily / 6% weekly loss limit, ATR filter, event blackout, stops only move up, no leverage, no shorts | unchanged |

ATR is the stock's average daily range over 14 days.

## Files

- `snapshots/latest.json`, `snapshots/<date>/`: each run's data, pictures and a README GitHub shows.
- `report/trade-sentinel-swing.html`: the full interactive page.
- Settings: `skills/trade-sentinel/presets/nse-swing/` (`max_hold_bars`, `max_wait_bars`,
  `max_target_atr`, `max_entry_gap_atr`); swing rules for the engines: `skills/trade-sentinel/prompts/swing.md`.

## Run it yourself

```bash
E="python3 skills/trade-sentinel/engine.py --dir trader-247-swing"
$E init --preset nse-swing            # once; it can share trader-247/bars
$E prepare                            # then the SCAN, SIGNAL, PLAN, RISK steps as in the main run
$E snapshot --out swing/snapshots
python3 report/build_report.py swing/snapshots/latest.json --out swing/report/trade-sentinel-swing.html \
    --snapshots-dir swing/snapshots --images
```

_Educational, not investment advice. You place every order yourself._
