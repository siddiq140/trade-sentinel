#!/usr/bin/env bash
# Start the trade-sentinel dashboard and scheduler: http://127.0.0.1:8247
cd "$(dirname "$0")"
E=skills/trade-sentinel/engine.py
[ -f trader-247/config.json ] || python3 "$E" init --preset nse
[ -s trader-247/universe.json ] && grep -q '"series"' trader-247/universe.json || python3 "$E" universe
exec python3 skills/trade-sentinel/server.py "$@"
