#!/bin/bash
# bounded supervisor: restarts hlpaper.py on crash, max 5 restarts/hour, then gives up (logged).
cd "$(dirname "$0")"; mkdir -p data; echo $$ > data/supervisor.pid
trap 'kill -TERM $child 2>/dev/null; wait $child; rm -f data/supervisor.pid; exit 0' TERM INT
restarts=(); while true; do
  python3 -u hlpaper.py >> data/bot.log 2>&1 & child=$!; echo $child > data/bot.pid; wait $child; rc=$?
  [ $rc -eq 2 ] && { echo "$(date) lock held by another instance; supervisor exiting" >> data/supervisor.log; exit 2; }
  now=$(date +%s); restarts=($(for t in "${restarts[@]}"; do [ $((now-t)) -lt 3600 ] && echo $t; done) $now)
  echo "$(date) bot exited rc=$rc; restarts last hour=${#restarts[@]}" >> data/supervisor.log
  echo "{\"ts\":\"$(date '+%F %T %Z')\",\"alert\":\"BOT_EXIT\",\"rc\":$rc}" >> data/alerts.jsonl
  [ ${#restarts[@]} -gt 5 ] && { echo "$(date) too many restarts; giving up" >> data/supervisor.log; exit 1; }
  sleep 30
done
