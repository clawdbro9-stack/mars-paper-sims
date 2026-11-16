"""GitHub Actions runner: start all three PAPER sim supervisors unchanged, write status.json/status.md,
commit state every 5 min and at exit, then stop cleanly so the queued run takes over."""
import json, os, signal, subprocess, time
from pathlib import Path
R = Path(__file__).resolve().parent; HL = R/"hl"; TG = R/"tg"; MACRO = R/"macro"
END = time.time() + int(os.environ.get("RUN_SECONDS", "20700"))
TZ = "America/Bogota"
STATE = ["hl/data", "tg/state.json", "tg/status.json", "tg/events.jsonl", "macro/data", "status.json", "status.md"]
def sh(*a): return subprocess.run(a, cwd=R, capture_output=True, text=True)
def rd(p):
    try: return json.loads(Path(p).read_text())
    except Exception as e: return {"error": type(e).__name__}
def alive(p):
    try: os.kill(int(Path(p).read_text()), 0); return True
    except Exception: return False
def status(note="running"):
    hb, ts, mh = rd(HL/"data/heartbeat.json"), rd(TG/"status.json"), rd(MACRO/"data/heartbeat.json")
    ms = rd(MACRO/"data/status.json")
    s = {"updated": time.strftime("%F %T %Z"), "runner": note, "run_url": os.environ.get("RUN_URL"),
         "hl": {"supervisor_alive": alive(HL/"data/supervisor.pid"), "bot_alive": alive(HL/"data/bot.pid"),
                **{k: hb.get(k) for k in ["strategy","ts","equity","realized","trades","wins","losses","entries_24h","last_scan_completed","halted","status","positions"]}},
         "tg": {"supervisor_alive": alive(TG/"supervisor.pid"), "sim_alive": alive(TG/"sim.pid"),
                **{k: ts.get(k) for k in ["time","cash","net_equity_estimated","realized","positions","seen","last_new_post","entry_count","rule_version","preview_ok"]}},
         "macro": {"supervisor_alive": alive(MACRO/"data/supervisor.pid"), "bot_alive": alive(MACRO/"data/bot.pid"),
                **{k: mh.get(k) for k in ["strategy","ts","equity","cash","realized","trades","wins","losses","n_positions","preview_ok","last_preview","last_new_post","posts_parsed","halted","status","target_equity","positions"]},
                **{k: ms.get(k) for k in ["time","net_equity_estimated","bet","started"]}}}
    (R/"status.json").write_text(json.dumps(s, indent=1))
    h, t, m = s["hl"], s["tg"], s["macro"]
    (R/"status.md").write_text(f"""# mars paper sims (PAPER only)
updated: {s['updated']} · runner: {note} · [run]({s['run_url']})

| sim | alive | equity | trades | last beat |
|---|---|---|---|---|
| hyperliquid {h.get('strategy')} | {h['bot_alive']} | ${h.get('equity')} | {h.get('trades')} ({h.get('wins')}W/{h.get('losses')}L) | {h.get('ts')} (scan {h.get('last_scan_completed')}) |
| tg mrpunk v{t.get('rule_version')} | {t['sim_alive']} | ${t.get('net_equity_estimated')} | {t.get('entry_count')} entries, {t.get('positions')} open | {t.get('time')} |
| macro {m.get('strategy')} | {m['bot_alive']} | ${m.get('equity')} | {m.get('trades')} ({m.get('wins')}W/{m.get('losses')}L), {m.get('n_positions')} open | {m.get('ts')} (preview {m.get('last_preview')}, ok={m.get('preview_ok')}) |
""")
def commit(msg):
    status(msg)
    sh("git", "add", "-A", *STATE)
    if sh("git", "diff", "--cached", "--quiet").returncode == 0: return
    sh("git", "commit", "-qm", msg)
    for _ in range(3):
        if sh("git", "push", "-q").returncode == 0: return
        sh("git", "pull", "-q", "--rebase"); time.sleep(5)
sh("git", "config", "user.name", "paper-sims-bot"); sh("git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
for p in ["hl/data/bot.lock","hl/data/bot.pid","hl/data/supervisor.pid","tg/sim.lock","tg/supervisor.lock","tg/sim.pid","tg/supervisor.pid","macro/data/bot.lock","macro/data/bot.pid","macro/data/supervisor.pid"]:
    (R/p).unlink(missing_ok=True)
hl = subprocess.Popen(["bash", "supervise.sh"], cwd=HL)
tg = subprocess.Popen(["python3", "supervisor.py"], cwd=TG)
macro = subprocess.Popen(["bash", "supervise.sh"], cwd=MACRO)
stop = False
def on_term(*_):
    global stop; stop = True
signal.signal(signal.SIGTERM, on_term); signal.signal(signal.SIGINT, on_term)
time.sleep(90); commit("state: run started")
last = time.time()
while not stop and time.time() < END:
    time.sleep(10)
    if time.time() - last >= 300: commit("state sync"); last = time.time()
hl.send_signal(signal.SIGTERM); tg.send_signal(signal.SIGTERM); macro.send_signal(signal.SIGTERM)
for p in (hl, tg, macro):
    try: p.wait(30)
    except Exception: p.kill()
commit("state: run ended, handing off")
