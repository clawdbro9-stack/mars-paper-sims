#!/bin/bash
cd "$(dirname "$0")"
cmd=${1:-status}
case "$cmd" in
  start)
    p=$(cat data/supervisor.pid 2>/dev/null)
    if [ -n "$p" ] && kill -0 "$p" 2>/dev/null; then echo already running pid=$p; exit 0; fi
    mkdir -p data
    nohup ./supervise.sh >> data/supervisor.log 2>&1 &
    sleep 0.2
    echo "started supervisor pid=$(cat data/supervisor.pid 2>/dev/null)"
    ;;
  stop)
    ./stop.sh
    ;;
  status)
    ./status.sh
    ;;
  idea)
    shift
    if [ -z "$*" ]; then echo 'usage: ./sim3.sh idea "short NVDA: reason"'; exit 1; fi
    python3 sim.py --idea "$*"
    ;;
  *)
    echo 'usage: ./sim3.sh start|stop|status|idea "short NVDA: reason"'
    exit 1
    ;;
esac
