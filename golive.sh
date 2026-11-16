#!/bin/bash
# Handoff: freeze local sims, seed repo with current state, push, start Actions. Needs `gh auth login` (repo+workflow scope)
# and an existing empty PUBLIC repo clawdbro9-stack/mars-paper-sims. If push fails, local sims are restarted.
set -u; cd "$(dirname "$0")"; REPO=clawdbro9-stack/mars-paper-sims
gh auth status >/dev/null 2>&1 || { echo "gh not authenticated"; exit 1; }
/workspace/mars-larb-paper/stop.sh; python3 /workspace/mars-tg-paper/stop.py; /workspace/mars-macro-paper/stop.sh; sleep 5
restart_local(){ (cd /workspace/mars-larb-paper && setsid nohup ./supervise.sh >/dev/null 2>&1 &); (cd /workspace/mars-tg-paper && setsid nohup python3 supervisor.py >/dev/null 2>&1 &); (cd /workspace/mars-macro-paper && setsid nohup ./supervise.sh >/dev/null 2>&1 &); echo "local restarted"; }
./build.sh && ./scan.sh || { restart_local; exit 1; }
git add -A && git -c user.name=paper-sims-bot -c user.email=paper-sims-bot@users.noreply.github.com commit -qm "seed: current local state" 
git remote get-url origin >/dev/null 2>&1 || git remote add origin https://github.com/$REPO.git
gh auth setup-git && git push -u origin main || { restart_local; exit 1; }
gh workflow run sims.yml -R $REPO || { restart_local; exit 1; }
echo "pushed + dispatched; verify run before considering local stopped"
