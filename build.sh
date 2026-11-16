#!/bin/bash
# copy LATEST local sim code + current state into this repo (no logs, no pids, no secrets)
set -e; cd "$(dirname "$0")"; rm -rf hl tg macro; mkdir -p hl/data tg macro/data
cp /workspace/mars-larb-paper/{hlpaper.py,supervise.sh,stop.sh,status.sh,RULES.md} hl/
for f in state.json events.jsonl trades.jsonl alerts.jsonl heartbeat.json; do cp /workspace/mars-larb-paper/data/$f hl/data/; done
cp /workspace/mars-tg-paper/{sim.py,signals.py,supervisor.py,stop.py,rules.json,README.md,state.json,status.json,events.jsonl} tg/
cp /workspace/mars-macro-paper/{sim.py,classify.py,prices.py,supervise.sh,sim3.sh,status.sh,stop.sh,RULES.md} macro/
for f in state.json events.jsonl alerts.jsonl heartbeat.json status.json; do cp /workspace/mars-macro-paper/data/$f macro/data/; done
echo built; md5sum hl/hlpaper.py tg/sim.py tg/signals.py tg/rules.json macro/sim.py macro/classify.py macro/prices.py
