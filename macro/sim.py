#!/usr/bin/env python3
"""Sim 3 macro social-arb. PAPER ONLY. No wallet, no keys, no real orders."""
import argparse, fcntl, html, json, os, re, signal, sys, time, traceback
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

import classify, prices

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
PREVIEW = "https://t.me/s/WatcherGuru"
TZ = ZoneInfo("America/Bogota")
CFG = dict(
    BANKROLL=25.0, MARGIN=6.0, MAX_POSITIONS=4,
    STOP_R=-0.50, TP_R=1.00, TP_FRAC=0.90, TRAIL_GAP=0.25,
    MAX_HOLD_S=24 * 3600, PERP_LEV=5.0,
    STOCK_SLIP=0.0005, PERP_TAKER=0.00045, PERP_SLIP=0.0002,
    PREVIEW_S=60, POS_POLL=5, MAX_POST_AGE_S=600,
    TARGET_2X=50.0, TARGET_3X=75.0,
)

def now():
    return time.time()

def bog(ts=None):
    return datetime.fromtimestamp(ts or now(), TZ)

def iso(ts=None):
    return bog(ts).strftime("%Y-%m-%d %H:%M:%S %Z")


def log(name, rec):
    DATA.mkdir(parents=True, exist_ok=True)
    rec = {"ts": iso(), "t": round(now(), 3), **rec}
    with (DATA / name).open("a") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")


def save_json(name, obj):
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = DATA / (name + ".tmp")
    with tmp.open("w") as f:
        json.dump(obj, f, indent=1, sort_keys=True)
        f.flush(); os.fsync(f.fileno())
    tmp.replace(DATA / name)


def load_json(name, default=None):
    p = DATA / name
    try:
        return json.loads(p.read_text())
    except FileNotFoundError:
        return default


def new_state():
    return dict(
        cash=CFG["BANKROLL"], realized=0.0, positions=[],
        launched=now(), halted=False, trades=0, wins=0, losses=0,
        baseline_id=None, seen_ids=[], seen_ideas=[],
        last_preview=None, preview_ok=False, posts_parsed=0,
        last_new_post=None, last_error=None,
    )


# ---- telegram preview (data only) ----
def fetch_preview():
    req_headers = {"User-Agent": "PublicPaperResearch/1.0"}
    import urllib.request
    req = urllib.request.Request(PREVIEW, headers=req_headers)
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read().decode("utf-8", "replace")


def parse_preview(s):
    out = []
    for part in re.split(r'(?=<div class="tgme_widget_message_wrap)', s):
        mid = re.search(r'data-post="WatcherGuru/(\d+)"', part)
        dt = re.search(r'<time[^>]*datetime="([^"]+)"', part)
        if not mid or not dt:
            continue
        texts = re.findall(
            r'<div class="tgme_widget_message_text(?![^>]*js-message_reply_text)[^>]*>(.*?)</div>',
            part, re.S,
        )
        text = " ".join(html.unescape(re.sub("<[^>]+>", " ", t)) for t in texts)
        text = re.sub(r"\s+", " ", text).strip()
        try:
            ts = datetime.fromisoformat(dt[1].replace("Z", "+00:00")).timestamp()
        except Exception:
            continue
        out.append(dict(id=int(mid[1]), timestamp=dt[1], t=ts, text=text))
    return sorted(out, key=lambda p: p["id"])


# ---- accounting ----
def slip_px(side, opening, px, venue):
    slip = CFG["STOCK_SLIP"] if venue == "stock" else (CFG["PERP_TAKER"] + CFG["PERP_SLIP"])
    # long open / short close: pay more; short open / long close: receive less
    take_up = (side == 1) == opening
    return px * (1 + slip) if take_up else px * (1 - slip)


def fee_rate(venue):
    return 0.0 if venue == "stock" else CFG["PERP_TAKER"]


def r_full(pos, px):
    gross = pos["side"] * (px - pos["entry"]) * pos["size0"]
    exit_fee = px * pos["size0"] * fee_rate(pos["venue"])
    return (gross - pos["entry_fee"] - exit_fee + pos.get("funding", 0.0)) / CFG["MARGIN"]


def px_for_r(pos, r):
    s, e, q = pos["side"], pos["entry"], pos["size0"]
    t = fee_rate(pos["venue"])
    # r*M = s*(px-e)*q - fee0 - px*q*t + f
    denom = s * q - q * t
    if abs(denom) < 1e-18:
        return e
    return (r * CFG["MARGIN"] + s * e * q + pos["entry_fee"] - pos.get("funding", 0.0)) / denom


def liq_price(side, entry, size, margin, max_lev):
    if size <= 0 or max_lev <= 0:
        return None
    mm = entry * size / (2 * max_lev)
    l = 1 / (2 * max_lev)
    denom = 1 - l * side
    if abs(denom) < 1e-12:
        return None
    return entry - side * (margin - mm) / size / denom


# ---- engine ----
class Bot:
    def __init__(s, data=None):
        global DATA
        if data:
            DATA = Path(data)
        DATA.mkdir(parents=True, exist_ok=True)
        s.S = load_json("state.json") or new_state()
        s.hl = {}
        s.hl_t = 0
        s._stop = False

    def ev(s, kind, **kw):
        log("events.jsonl", dict(event=kind, **kw))

    def equity(s):
        eq = s.S["cash"]
        for p in s.S["positions"]:
            px = p.get("last_px", p["entry"])
            remain = p["size"] / p["size0"] if p["size0"] else 0
            eq += CFG["MARGIN"] * remain + p["side"] * (px - p["entry"]) * p["size"]
            eq -= px * p["size"] * fee_rate(p["venue"]) + p.get("funding_open", 0.0)
        return eq

    def refresh_hl(s, force=False):
        if not force and now() - s.hl_t < 20 and s.hl:
            return
        s.hl = prices.hl_meta()
        s.hl_t = now()

    def quote(s, symbol, venue):
        return prices.quote(symbol, venue, hl_cache=s.hl if venue == "perp" else None)

    # ---- entries ----
    def consider(s, idea, post_id=None, text="", age_s=None):
        """idea is a classify() / parse_idea() dict. text is untrusted data, logged only."""
        rec = dict(post_id=post_id, score=idea.get("score"), side=idea.get("side"),
                   symbol=idea.get("symbol"), event=idea.get("event"),
                   source=idea.get("source"), reasons=idea.get("reasons"),
                   text=(text or "")[:500])
        if idea.get("ping"):
            log("alerts.jsonl", dict(alert="ping Captain", **rec, tradable=bool(idea.get("tradable"))))
            s.ev("ping", **{k: rec[k] for k in rec if k not in ("text", "event")}, idea_event=idea.get("event"))
        if not idea.get("tradable"):
            s.ev("skip", reason="below conviction or not tradable", **{k: rec[k] for k in rec if k not in ("text", "event")}, idea_event=idea.get("event"))
            return False
        if age_s is not None and age_s > CFG["MAX_POST_AGE_S"] and idea.get("source") != "captain":
            s.ev("skip", reason=f"headline {age_s:.0f}s old", symbol=idea.get("symbol"), post_id=post_id)
            return False
        return s.try_enter(idea, text=text, post_id=post_id)

    def gate(s, symbol):
        if s.S.get("halted"):
            return "halted"
        if any(p["symbol"] == symbol for p in s.S["positions"]):
            return f"already have a {symbol} position"
        if len(s.S["positions"]) >= CFG["MAX_POSITIONS"]:
            return "4 position cap"
        if s.S["cash"] < CFG["MARGIN"] * 1.05:
            return "insufficient cash for a $6 bet plus fee reserve"
        return None

    def try_enter(s, idea, text="", post_id=None):
        symbol, venue, direction = idea["symbol"], idea["venue"], idea["direction"]
        g = s.gate(symbol)
        if g:
            s.ev("skip", reason=g, symbol=symbol, post_id=post_id)
            return False
        try:
            if venue == "perp":
                s.refresh_hl()
            q = s.quote(symbol, venue)
        except Exception as e:
            s.ev("skip", reason=f"quote failed: {type(e).__name__}: {e}"[:200], symbol=symbol)
            log("alerts.jsonl", dict(alert="ping Captain", text=text[:500], symbol=symbol,
                                     side=idea.get("side"), reason="could not price, no entry", post_id=post_id))
            return False
        ok, why = prices.usable_for_entry(q)
        if not ok:
            s.ev("skip", reason=why, symbol=symbol, post_id=post_id, session=q.get("session"))
            # still ping; the setup may be valid at the open
            log("alerts.jsonl", dict(alert="ping Captain", text=text[:500], symbol=symbol,
                                     side=idea.get("side"), reason=why, queued=False, post_id=post_id))
            return False
        side = direction
        mid = q["px"]
        if venue == "perp":
            max_lev = float(q.get("max_lev") or 1)
            lev = min(CFG["PERP_LEV"], max_lev)
        else:
            max_lev, lev = 1.0, 1.0
        entry = slip_px(side, True, mid, venue)
        size = (CFG["MARGIN"] * lev) / entry
        fee = entry * size * fee_rate(venue)
        if s.S["cash"] < CFG["MARGIN"] + fee:
            s.ev("skip", reason="cash short including entry fee", symbol=symbol)
            return False
        liq = liq_price(side, entry, size, CFG["MARGIN"], max_lev) if venue == "perp" else None
        pos = dict(
            id=f"{symbol}-{int(now())}", symbol=symbol, venue=venue, side=side,
            entry=entry, mid=mid, size0=size, size=size, lev=lev, max_lev=max_lev,
            entry_fee=fee, funding=0.0, funding_open=0.0, opened=now(),
            last_px=entry, last_ok=now(), last_fund_hr=int(now() // 3600),
            peak_r=0.0, trail_armed=False, tp_done=False, realized_slices=[],
            liq=liq, source=idea.get("source"), event=idea.get("event"),
            post_id=post_id, score=idea.get("score"), reason=(text or "")[:400],
        )
        pos["stop_px"] = px_for_r(pos, CFG["STOP_R"])
        pos["tp_px"] = px_for_r(pos, CFG["TP_R"])
        # -50% stop must sit inside liquidation for perps
        if liq is not None:
            if side == 1 and not (pos["stop_px"] > liq):
                s.ev("skip", reason="stop would not hit before liq", symbol=symbol, stop=pos["stop_px"], liq=liq)
                return False
            if side == -1 and not (pos["stop_px"] < liq):
                s.ev("skip", reason="stop would not hit before liq", symbol=symbol, stop=pos["stop_px"], liq=liq)
                return False
        s.S["cash"] -= CFG["MARGIN"] + fee
        s.S["positions"].append(pos)
        reason = (f"{'LONG' if side==1 else 'SHORT'} {symbol} ({venue}) at {entry:.6g}, "
                  f"{lev:g}x, $6 margin, -50% stop {pos['stop_px']:.6g}, +100% tp {pos['tp_px']:.6g}; "
                  f"score {idea.get('score')} event {idea.get('event')} source {idea.get('source')}")
        log("trades.jsonl", dict(action="open", id=pos["id"], symbol=symbol, venue=venue,
                                 side=side, entry=entry, size=size, lev=lev, fee=fee,
                                 stop_px=pos["stop_px"], tp_px=pos["tp_px"], liq=liq,
                                 reason=reason, post_id=post_id, score=idea.get("score")))
        log("alerts.jsonl", dict(alert="ENTRY", text=reason, post_id=post_id))
        s.ev("entry", id=pos["id"], symbol=symbol, side=side, entry=entry, lev=lev)
        s.persist()
        return True

    # ---- exits ----
    def close_slice(s, pos, frac, px, why, trig_px):
        size = pos["size"] if frac >= 1 else pos["size"] * frac
        fill = slip_px(pos["side"], False, px, pos["venue"])
        gross = pos["side"] * (fill - pos["entry"]) * size
        fee = fill * size * fee_rate(pos["venue"])
        margin_back = CFG["MARGIN"] * size / pos["size0"]
        fshare = pos.get("funding_open", 0.0) * size / pos["size"] if pos["size"] else 0.0
        pos["funding_open"] = pos.get("funding_open", 0.0) - fshare
        pos["size"] = pos["size"] - size
        s.S["cash"] += margin_back + gross - fee + fshare
        sl = dict(size=size, px=fill, gross=gross, fee=fee, why=why, trig_px=trig_px,
                  gap=(fill - trig_px) * pos["side"] if trig_px else None)
        pos["realized_slices"].append(sl)
        log("trades.jsonl", dict(action="close_slice", id=pos["id"], **sl, remaining=pos["size"]))
        if pos["size"] <= 1e-12:
            s.finish(pos)

    def finish(s, pos):
        net = sum(x["gross"] - x["fee"] for x in pos["realized_slices"]) - pos["entry_fee"] + pos.get("funding", 0.0)
        win = net > 0
        s.S["realized"] += net
        s.S["trades"] += 1
        if win:
            s.S["wins"] += 1
        else:
            s.S["losses"] += 1
        rec = dict(action="trade_complete", id=pos["id"], symbol=pos["symbol"], venue=pos["venue"],
                   net_pnl=net, ret_on_margin=net / CFG["MARGIN"], result="win" if win else "loss",
                   slices=len(pos["realized_slices"]), funding=pos.get("funding", 0.0),
                   entry_fee=pos["entry_fee"], peak_r=pos["peak_r"], equity=s.S["cash"])
        log("trades.jsonl", rec)
        log("alerts.jsonl", dict(alert="EXIT", **rec))
        s.S["positions"] = [p for p in s.S["positions"] if p is not pos and p.get("id") != pos["id"]]
        s.ev("exit", **{k: rec[k] for k in ("id", "symbol", "net_pnl", "result")})

    def accrue_funding(s, pos, fund_rate, mark):
        if pos["venue"] != "perp" or fund_rate is None:
            return
        hr = int(now() // 3600)
        while pos["last_fund_hr"] < hr:
            pos["last_fund_hr"] += 1
            amt = -pos["side"] * fund_rate * mark * pos["size"]
            pos["funding"] += amt
            pos["funding_open"] = pos.get("funding_open", 0.0) + amt
            s.ev("funding", id=pos["id"], rate=fund_rate, usd=amt)

    def manage(s, pos, q):
        px = q["px"]
        s.accrue_funding(pos, q.get("funding"), q.get("mark", px))
        pos["last_px"] = px
        pos["last_ok"] = now()
        r = r_full(pos, px)
        pos["peak_r"] = max(pos["peak_r"], r)
        crossed = lambda lvl: (px - lvl) * pos["side"] <= 0
        if crossed(pos["stop_px"]):
            s.close_slice(pos, 1, px, "stop -50% of $6 equity", pos["stop_px"])
            return
        if pos["trail_armed"]:
            tpx = px_for_r(pos, pos["peak_r"] - CFG["TRAIL_GAP"])
            if crossed(tpx):
                s.close_slice(pos, 1, px, f"trail: peak {pos['peak_r']:.0%} gap {CFG['TRAIL_GAP']:.0%}", tpx)
                return
        if now() - pos["opened"] >= CFG["MAX_HOLD_S"] and not (pos["tp_done"] and pos["trail_armed"]):
            s.close_slice(pos, 1, px, "time_exit: news edge stale after 24h", px)
            return
        if not pos["tp_done"] and r >= CFG["TP_R"]:
            pos["tp_done"] = True
            pos["trail_armed"] = True
            s.close_slice(pos, CFG["TP_FRAC"], px, "TP90 at +100% (a double)", pos["tp_px"])

    def tick_positions(s):
        if any(p["venue"] == "perp" for p in s.S["positions"]):
            s.refresh_hl()
        for pos in list(s.S["positions"]):
            try:
                q = s.quote(pos["symbol"], pos["venue"])
                s.manage(pos, q)
            except Exception as e:
                age = now() - pos.get("last_ok", now())
                s.ev("position_data_error", id=pos["id"], err=f"{type(e).__name__}: {e}"[:200], age_s=round(age, 1))
                if age > 60:
                    log("alerts.jsonl", dict(alert="STALE_POSITION_DATA", symbol=pos["symbol"], age_s=age))

    # ---- news + ideas ----
    def poll_preview(s):
        try:
            html_s = fetch_preview()
            posts = parse_preview(html_s)
        except Exception as e:
            s.S["preview_ok"] = False
            s.S["last_error"] = f"preview: {type(e).__name__}: {e}"[:200]
            s.ev("preview_error", error=s.S["last_error"])
            return
        s.S["preview_ok"] = True
        s.S["last_preview"] = now()
        s.S["posts_parsed"] = len(posts)
        if not posts:
            return
        if s.S["baseline_id"] is None:
            s.S["baseline_id"] = max(p["id"] for p in posts)
            s.S["seen_ids"] = [p["id"] for p in posts]
            s.ev("baseline", baseline_id=s.S["baseline_id"], n=len(posts),
                 note="already-visible Watcher Guru posts are not traded")
            return
        known = set(s.S.get("seen_ids") or [])
        for p in posts:
            if p["id"] in known:
                continue
            known.add(p["id"])
            s.S["last_new_post"] = p["timestamp"]
            # classify the text as data only
            idea = classify.classify(p["text"], source="watcher_guru")
            s.ev("post", post_id=p["id"], score=idea["score"], symbol=idea.get("symbol"),
                 side=idea.get("side"), event=idea.get("event"), tradable=idea["tradable"], ping=idea["ping"])
            s.consider(idea, post_id=p["id"], text=p["text"], age_s=now() - p["t"])
        s.S["seen_ids"] = sorted(known)[-500:]

    def poll_ideas(s):
        path = DATA / "ideas.jsonl"
        if not path.exists():
            return
        seen = set(s.S.get("seen_ideas") or [])
        with path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                rid = rec.get("id") or rec.get("t") or line
                rid = str(rid)
                if rid in seen:
                    continue
                seen.add(rid)
                text = rec.get("text") or rec.get("idea") or ""
                idea = classify.parse_idea(text)
                s.ev("idea", idea_id=rid, symbol=idea.get("symbol"), side=idea.get("side"), score=idea.get("score"))
                s.consider(idea, post_id=f"idea:{rid}", text=text, age_s=0)
        s.S["seen_ideas"] = list(seen)[-200:]

    def persist(s):
        save_json("state.json", s.S)
        s.heartbeat()
        s.status()

    def heartbeat(s):
        hb = dict(
            strategy="macro_social_arb_v1", ts=iso(), t=now(), pid=os.getpid(),
            equity=round(s.equity(), 6), cash=s.S["cash"], realized=s.S["realized"],
            trades=s.S["trades"], wins=s.S["wins"], losses=s.S["losses"],
            positions=[{k: p[k] for k in ("id", "symbol", "venue", "side", "entry", "lev", "last_px", "stop_px", "tp_px", "peak_r", "opened") if k in p}
                       | {"r_full": r_full(p, p.get("last_px", p["entry"]))}
                       for p in s.S["positions"]],
            n_positions=len(s.S["positions"]),
            preview_ok=s.S.get("preview_ok"), last_preview=iso(s.S["last_preview"]) if s.S.get("last_preview") else None,
            posts_parsed=s.S.get("posts_parsed", 0), baseline_id=s.S.get("baseline_id"),
            last_new_post=s.S.get("last_new_post"), halted=s.S.get("halted"),
            target_equity=CFG["TARGET_2X"], target_3x=CFG["TARGET_3X"],
            status="ok" if s.S.get("preview_ok") else (s.S.get("last_error") or "starting"),
        )
        save_json("heartbeat.json", hb)

    def status(s):
        save_json("status.json", dict(
            time=datetime.now(TZ).isoformat(),
            pid=os.getpid(),
            mode="forward_paper_only",
            cash=s.S["cash"],
            net_equity_estimated=s.equity(),
            realized=s.S["realized"],
            positions=len(s.S["positions"]),
            trades=s.S["trades"], wins=s.S["wins"], losses=s.S["losses"],
            posts_parsed=s.S.get("posts_parsed", 0),
            preview_ok=s.S.get("preview_ok"),
            last_preview=iso(s.S["last_preview"]) if s.S.get("last_preview") else None,
            last_new_post=s.S.get("last_new_post"),
            baseline_id=s.S.get("baseline_id"),
            target_equity=CFG["TARGET_2X"],
            local_alerts_only=True,
            stock_hours="US RTH only, 1x, Yahoo public chart",
            perp_leverage="5x isolated, -50% stop inside liq",
            bet="$6, max 4 positions",
            started=iso(s.S.get("launched")),
        ))

    def run(s):
        s._stop = False
        def halt(*_):
            s._stop = True
        signal.signal(signal.SIGTERM, halt)
        signal.signal(signal.SIGINT, halt)
        s.ev("start", pid=os.getpid(), cfg={k: CFG[k] for k in CFG})
        last_preview = 0
        errs = 0
        while not s._stop:
            try:
                if now() - last_preview >= CFG["PREVIEW_S"] or last_preview == 0:
                    s.poll_preview()
                    last_preview = now()
                s.poll_ideas()
                s.tick_positions()
                s.S["last_error"] = None
                s.persist()
                errs = 0
            except Exception as e:
                errs += 1
                s.S["last_error"] = f"{type(e).__name__}: {e}"[:200]
                s.ev("error", err=s.S["last_error"], n=errs, tb=traceback.format_exc()[-800:])
                s.heartbeat()
                if errs >= 20:
                    s.ev("fatal", reason="20 consecutive errors")
                    sys.exit(3)
                time.sleep(min(60, 2 ** errs))
            for _ in range(CFG["POS_POLL"] if s.S["positions"] else 5):
                if s._stop:
                    break
                time.sleep(1)
        s.persist()
        s.ev("stop", pid=os.getpid())


def append_idea(text):
    DATA.mkdir(parents=True, exist_ok=True)
    rec = dict(id=f"idea-{int(now()*1000)}", text=text, ts=iso(), t=now())
    with (DATA / "ideas.jsonl").open("a") as f:
        f.write(json.dumps(rec) + "\n")
    idea = classify.parse_idea(text)
    print(json.dumps(dict(recorded=rec["id"], parsed=idea), indent=2))
    return rec, idea


def main():
    global DATA
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=None)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--idea", default=None, help="inject a headline/trade idea and exit")
    a = ap.parse_args()
    DATA = Path(a.data) if a.data else HERE / "data"
    DATA.mkdir(parents=True, exist_ok=True)
    if a.idea is not None:
        append_idea(a.idea)
        return
    lock = (DATA / "bot.lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("another instance holds the lock; exiting")
        sys.exit(2)
    lock.write(str(os.getpid())); lock.flush()
    bot = Bot(str(DATA))
    if a.once:
        bot.poll_preview()
        bot.poll_ideas()
        bot.tick_positions()
        bot.persist()
        return
    bot.run()


if __name__ == "__main__":
    main()
