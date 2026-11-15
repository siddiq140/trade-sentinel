# SWING ADDENDUM  (applies on top of the engine prompt when config "style" is "swing")

This run is for SWING trades held 1-{{MAX_HOLD_BARS}} trading days after entry. Everything in the
engine prompt still applies; these rules are added, never relaxed.

SIGNAL
- Prefer setups that can trigger within the next 1-{{MAX_WAIT_BARS}} sessions near the last close.
- -15 when the last close is more than {{MAX_ENTRY_GAP_ATR}} ATR beyond the trigger level (the
  entry would need a deep pullback that a swing trader cannot wait for).
- -10 when the nearest opposing level is closer than 1 ATR (no room to move in a few days).
- Say in "evidence" how far the last close is from the trigger level, in ATR.

PLAN
- entry_zone must contain the last close, or sit within {{MAX_ENTRY_GAP_ATR}} ATR of it. If the
  setup's own entry rule puts it further away, return "NO_PLAN" with that reason.
- target: the NEARER of the next opposing level and entry_mid + {{MAX_TARGET_ATR}} x ATR14 (what a
  stock can usually travel in {{MAX_HOLD_BARS}} days). Never further.
- stop: structural, as in the engine prompt. If that makes rr < min_rr, keep it: the RISK
  engine will block it. Do not tighten the stop to rescue rr.
- time_stop_bars: sessions to wait for entry, at most {{MAX_WAIT_BARS}}.
- max_hold_bars: add this integer field, at most {{MAX_HOLD_BARS}}: exit at the close of that
  session after entry if neither stop nor target has hit.
- change_my_mind must include one time-based event, e.g. "no follow-through within 3 sessions".

RISK
- Code adds gate G6: entry within {{MAX_ENTRY_GAP_ATR}} ATR of the last close, target within
  {{MAX_TARGET_ATR}} ATR of entry_mid, max_hold_bars <= {{MAX_HOLD_BARS}}. Report it like G1-G5.
