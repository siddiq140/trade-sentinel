#!/usr/bin/env python3
"""Run one of the five engines (scan, signal, plan, risk, monitor) through the Claude API.

    python3 claude_engines.py scan    [--dir trader-247] [--cycle ID]
    python3 claude_engines.py signal
    ...

Reads cycles/<id>/<engine>_input.json (written by engine.py), sends it with the
engine's prompt from prompts/, and saves the reply to cycles/<id>/<engine>.json.
It never validates or acts on the reply: run `engine.py validate <engine>` next.

Needs `pip install anthropic` and credentials (ANTHROPIC_API_KEY, or an
`ant auth login` profile). Model and effort come from config.json:
  "claude_model": "claude-opus-5-5",
  "claude_effort": {"scan": "low", "signal": "high", "plan": "high", "risk": "medium", "monitor": "low"}
"""
import argparse
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINES = ("scan", "signal", "plan", "risk", "monitor")
INPUT_FILE = {"scan": "scan_input.json", "signal": "signal_input.json", "plan": "plan_input.json",
              "risk": "risk_input.json", "monitor": "monitor_input.json"}

ENVELOPE = """
HAND-OFF FORMAT (applies to everything above)
Reply with one JSON object and nothing else: no prose, no markdown fences.
{"cycle_id": "%s", "engine": "%s", "version": "1.0",
 "generated_at": "<UTC time, ISO 8601>", "items": [ ... ], "warnings": [ ... ]}
Put anything you could not evaluate in "warnings". Code validates every item;
an item with a missing or malformed field is dropped, so follow the item shape exactly.
"""


def available():
    """(True, '') when the SDK is installed and some credential source is configured."""
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False, "the anthropic package is not installed (pip install anthropic)"
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN") or \
            os.environ.get("ANTHROPIC_PROFILE") or os.path.isdir(os.path.expanduser("~/.config/anthropic")):
        return True, ""
    return False, "no Claude credentials (set ANTHROPIC_API_KEY or run `ant auth login`)"


def build_system(engine, payload, config):
    text = open(os.path.join(HERE, "prompts", engine + ".md")).read()
    values = {k: v for k, v in payload.items() if k.isupper()}
    values.setdefault("MIN_LIQUIDITY", config.get("min_liquidity"))
    values.setdefault("MAX_CANDIDATES", config.get("max_candidates", 40))
    values.setdefault("PASS_MARK", config.get("pass_mark", 70))
    values.setdefault("INTERVAL", config.get("monitor_interval_min", 15))
    values.setdefault("MARKET_SHOCK_PCT", config.get("market_shock_pct", 2.0))
    text = re.sub(r"\{\{(\w+)\}\}", lambda m: str(values.get(m.group(1), m.group(0))), text)
    market = config.get("market")
    if market:
        text += "\nMARKET\n%s. Prices are in the market's own currency.\n" % market
    if config.get("allow_short") is False:
        text += "Overnight short positions are not possible in this market: prefer LONG setups.\n"
    return text + ENVELOPE % (payload.get("cycle_id", ""), engine)


def call(engine, data_dir, cycle, config, log=print):
    import anthropic

    path_in = os.path.join(data_dir, "cycles", cycle, INPUT_FILE[engine])
    path_out = os.path.join(data_dir, "cycles", cycle, engine + ".json")
    payload = json.load(open(path_in))
    payload.pop("engine_prompt", None)
    system = build_system(engine, payload, config)
    user = "INPUT\n" + json.dumps(payload, separators=(",", ":"))
    model = config.get("claude_model", "claude-opus-5-5")
    effort = (config.get("claude_effort") or {}).get(engine, "high")
    client = anthropic.Anthropic()
    kwargs = dict(model=model, max_tokens=64000, system=system,
                  messages=[{"role": "user", "content": user}],
                  output_config={"effort": effort},
                  betas=["server-side-fallback-2026-07-01"])
    t0 = time.time()
    try:
        with client.beta.messages.stream(fallbacks="default", **kwargs) as stream:
            msg = stream.get_final_message()
    except TypeError:  # an SDK release that predates the fallbacks argument
        with client.beta.messages.stream(extra_body={"fallbacks": "default"}, **kwargs) as stream:
            msg = stream.get_final_message()
    if msg.stop_reason == "refusal":
        cat = getattr(msg.stop_details, "category", None) if msg.stop_details else None
        raise RuntimeError("%s engine: the model declined (%s)" % (engine, cat))
    if msg.stop_reason == "max_tokens":
        raise RuntimeError("%s engine: reply hit max_tokens and is incomplete" % engine)
    text = "".join(b.text for b in msg.content if b.type == "text").strip()
    with open(path_out, "w") as f:
        f.write(text)
    u = msg.usage
    log("%s engine: %s, %.0fs, %s in / %s out tokens -> %s"
        % (engine, msg.model, time.time() - t0, u.input_tokens, u.output_tokens, path_out))
    return path_out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("engine", choices=ENGINES)
    ap.add_argument("--dir", default="trader-247")
    ap.add_argument("--cycle")
    a = ap.parse_args(argv)
    ok, why = available()
    if not ok:
        print("ERROR: " + why, file=sys.stderr)
        sys.exit(2)
    cycle = a.cycle or open(os.path.join(a.dir, "current_cycle")).read().strip()
    config = json.load(open(os.path.join(a.dir, "config.json")))
    try:
        call(a.engine, a.dir, cycle, config)
    except Exception as ex:
        import anthropic
        if isinstance(ex, anthropic.RateLimitError):
            print("ERROR: rate limited; the next run will try again", file=sys.stderr)
        elif isinstance(ex, anthropic.APIStatusError):
            print("ERROR: API %s: %s" % (ex.status_code, ex.message), file=sys.stderr)
        elif isinstance(ex, anthropic.APIConnectionError):
            print("ERROR: could not reach the Claude API", file=sys.stderr)
        else:
            print("ERROR: %s" % ex, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
