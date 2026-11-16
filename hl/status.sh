#!/bin/bash
cd "$(dirname "$0")"
for f in supervisor.pid bot.pid; do p=$(cat data/$f 2>/dev/null); echo "$f=$p alive=$(kill -0 $p 2>/dev/null && echo yes || echo no)"; done
cat data/heartbeat.json; echo; echo "--- last trades"; tail -n 5 data/trades.jsonl 2>/dev/null; echo "--- alerts"; tail -n 5 data/alerts.jsonl 2>/dev/null
