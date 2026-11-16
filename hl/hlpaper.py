#!/usr/bin/env python3
"""Hyperliquid PAPER momentum simulator (no wallet, no keys, no orders, no AI).

Data: documented public POST https://api.hyperliquid.xyz/info only
  metaAndAssetCtxs (w20), candleSnapshot 1m (w20+), l2Book (w2).  Budget << 1200 weight/min.

Accounting (all on ORIGINAL $6 isolated margin):
  R_full(px) = (side*(px-entry)*full_size - entry_fee - est_exit_fee_full + funding)/MARGIN
  All triggers (stop -10% ceiling, per-position versioned trailing/TP90) use R_full(px) mapped to an
  underlying price, so the runner keeps the same price trail after partial close.
  Realized trade PnL = sum of slice PnL (net of fees) + funding - entry fee. One round trip = one trade
  (TP90 slice + runner counted together).
"""
import argparse, fcntl, json, math, os, signal, sys, time, traceback
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import requests
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
API = "https://api.hyperliquid.xyz/info"
TZ = ZoneInfo("America/Bogota")

CFG = dict(
    BANKROLL=25.0, MARGIN=6.0, MAX_LEV=30.0, TAKER=0.00045,       # tier-0 taker 0.045% (docs fees page)
    EXTRA_SLIP=0.0002,                                            # added on top of simulated book walk
    MIN_NOTIONAL=10.0,                                            # docs: "Order must have minimum value of $10."
    STOP_R=-0.10, TRAIL_ARM=0.30, TRAIL_GAP=0.10, TP_R=0.40, TP_FRAC=0.90,  # legacy defaults for pre-v4 positions
    MAX_LOSS_USD=0.50, MAX_ENTRIES_DAY=10, MAX_LOSSES_DAY=2, EQUITY_STOP=20.0,
    MAX_POSITIONS=3, MAX_INITIAL_MARGIN=18.0, MAIN_MAX_HOLD_S=7200,
    STRATEGY="scalp_momentum_v4", MIN_SCALP_LEV=15.0, SCALP_TP_R=0.20, SCALP_TRAIL_ARM=0.12, SCALP_TRAIL_GAP=0.05,
    SCALP_HOLD_S=300,
    MIN_DAY_VLM=50e6, TOP_N=8, LOOKBACK=12, VOL_N=20, VOL_MULT=4.0,
    MAX_SPREAD=0.0002, DEPTH_MULT=5.0, DEPTH_BAND=0.002, MAX_ADVERSE_FUND_HR=0.0001,
    CHASE_ATR=0.20, LIQ_BUFFER=1.5, MIN_STOP=0.002,
    POS_POLL=3.0, SCAN_EVERY=60, STALE_S=20, HTTP_TIMEOUT=8,
    SCALP_LOOKBACK=15, BODY_ATR=1.0, MIN_STRENGTH=0.75, CLOSE_LOC=0.85,
    SIGNAL_AGE_MS=20000, LOSS_COOLDOWN_S=3600, SCRATCH_S=120, SCRATCH_MIN_R=0.02,
    RR_MIN=1.5, CANDLE_N=60,
)

def now(): return time.time()
def bog(ts=None): return datetime.fromtimestamp(ts or now(), TZ)
def iso(ts=None): return bog(ts).strftime("%Y-%m-%d %H:%M:%S %Z")

class Store:
    def __init__(s, d): s.d = d; os.makedirs(d, exist_ok=True)
    def p(s, n): return os.path.join(s.d, n)
    def log(s, n, rec):
        rec = {"ts": iso(), "t": round(now(), 3), **rec}
        with open(s.p(n), "a") as f: f.write(json.dumps(rec, sort_keys=True) + "\n")
    def save(s, st):
        tmp = s.p("state.json.tmp")
        with open(tmp, "w") as f: json.dump(st, f, indent=1, sort_keys=True); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, s.p("state.json"))
    def load(s):
        try: return json.load(open(s.p("state.json")))
        except FileNotFoundError: return None

def new_state():
    return dict(cash=CFG["BANKROLL"], realized=0.0, position=None, baseline={}, launched=now(),
                day=None, day_entries=0, day_losses=0, halted=False, trades=0, wins=0, losses=0,
                gross_win=0.0, gross_loss=0.0, last_scan_candle={}, seen_signals=[], last_loss_t=None,
                strategy=CFG["STRATEGY"])

# ---------------- market data ----------------
class HL:
    def __init__(s): s.sess = requests.Session(); s.backoff = 0
    def post(s, body):
        for i, w in enumerate([2, 10, 30, None]):
            try:
                r = s.sess.post(API, json=body, timeout=CFG["HTTP_TIMEOUT"])
                if r.status_code == 429: raise RuntimeError("429 rate limited")
                r.raise_for_status(); return r.json()
            except Exception:
                if w is None: raise
                time.sleep(w)
    def ctx(s):
        m, c = s.post({"type": "metaAndAssetCtxs"})
        out = {}
        for a, x in zip(m["universe"], c):
            out[a["name"]] = dict(meta=a, ctx=x)
        return out
    def candles(s, coin, n=None):
        n = CFG["CANDLE_N"] if n is None else n
        e = int(now() * 1000); st = e - (n + 2) * 60000
        return s.post({"type": "candleSnapshot", "req": {"coin": coin, "interval": "1m", "startTime": st, "endTime": e}})
    def book(s, coin): return s.post({"type": "l2Book", "coin": coin})

def book_stats(bk, notional):
    bids = [(float(l["px"]), float(l["sz"])) for l in bk["levels"][0]]
    asks = [(float(l["px"]), float(l["sz"])) for l in bk["levels"][1]]
    if not bids or not asks: raise ValueError("empty book")
    bb, ba = bids[0][0], asks[0][0]; mid = (bb + ba) / 2
    band = CFG["DEPTH_BAND"]
    dbid = sum(p * q for p, q in bids if p >= mid * (1 - band))
    dask = sum(p * q for p, q in asks if p <= mid * (1 + band))
    return dict(bid=bb, ask=ba, mid=mid, spread=(ba - bb) / mid, depth_bid=dbid, depth_ask=dask,
                bids=bids, asks=asks, time=bk.get("time", 0) / 1000)

def walk(levels, notional):
    """VWAP for taking `notional` USD through levels; None if insufficient."""
    left, cost, qty = notional, 0.0, 0.0
    for p, q in levels:
        take = min(left, p * q); cost += take; qty += take / p; left -= take
        if left <= 1e-9: break
    if left > 1e-9: return None
    return cost / qty

def fill_px(side, opening, bs, notional):
    # long open / short close take asks; long close / short open hit bids
    take_asks = (side == 1) == opening
    v = walk(bs["asks"] if take_asks else bs["bids"], notional)
    if v is None: return None
    return v * (1 + CFG["EXTRA_SLIP"]) if take_asks else v * (1 - CFG["EXTRA_SLIP"])

def round_sz(sz, dec): f = 10 ** dec; return math.floor(sz * f) / f

# ---------------- position math ----------------
def r_full(pos, px):
    gross = pos["side"] * (px - pos["entry"]) * pos["size0"]
    exit_fee = px * pos["size0"] * CFG["TAKER"]
    return (gross - pos["entry_fee"] - exit_fee + pos["funding"]) / CFG["MARGIN"]

def px_for_r(pos, r):
    """underlying price where R_full == r (inverse of r_full, linear in px)."""
    s, e, q, t = pos["side"], pos["entry"], pos["size0"], CFG["TAKER"]
    # r*M = s*(px-e)*q - fee0 - px*q*t + f  ->  px*(s*q - q*t) = r*M + s*e*q + fee0 - f
    return (r * CFG["MARGIN"] + s * e * q + pos["entry_fee"] - pos["funding"]) / (s * q - q * t)

def liq_price(side, entry, size, margin, max_lev):
    # docs: liq = price - side*margin_available/size/(1 - l*side), l = 1/maint_lev, maint lev = 2*maxLev
    mm = entry * size / (2 * max_lev); l = 1 / (2 * max_lev)
    return entry - side * (margin - mm) / size / (1 - l * side)

def plan(side, entry_px, stop_px, maxlev, sz_dec):
    """size the trade so loss at stop (incl fees+slip) <= MAX_LOSS_USD."""
    d = abs(entry_px - stop_px) / entry_px
    cost = 2 * CFG["TAKER"] + 2 * CFG["EXTRA_SLIP"] + 0.0005  # +0.05% stop-gap allowance
    notional = CFG["MAX_LOSS_USD"] / (d + cost)
    lev = min(notional / CFG["MARGIN"], CFG["MAX_LEV"], maxlev)
    notional = lev * CFG["MARGIN"]
    size = round_sz(notional / (entry_px * 1.002), sz_dec); notional = size * entry_px  # 0.2% headroom so fill slip keeps lev <= cap
    if notional < CFG["MIN_NOTIONAL"]: return None, f"notional {notional:.2f} < $10 venue minimum (stop {d:.2%} too wide)"
    lev = notional / CFG["MARGIN"]
    liq = liq_price(side, entry_px, size, CFG["MARGIN"], maxlev)
    liq_d = abs(entry_px - liq) / entry_px
    if liq_d < CFG["LIQ_BUFFER"] * d: return None, f"liq {liq_d:.2%} not safely beyond stop {d:.2%}"
    return dict(size=size, notional=notional, lev=lev, liq=liq, stop_dist=d,
                modeled_loss=notional * (d + cost)), None

# ---------------- signal ----------------
def atr(cs, n=14):
    tr = [max(float(c["h"]) - float(c["l"]), abs(float(c["h"]) - float(p["c"])), abs(float(c["l"]) - float(p["c"]))) for p, c in zip(cs[:-1], cs[1:])]
    return sum(tr[-n:]) / min(n, len(tr))

def detect(cs, now_ms):
    """cs: candles ascending. Uses only CLOSED candles. Returns (signal|None, info)."""
    closed = [c for c in cs if int(c["T"]) < now_ms]
    need = max(CFG["LOOKBACK"], CFG["VOL_N"]) + 15
    if len(closed) < need: return None, dict(reason=f"incomplete candles {len(closed)}<{need}")
    c = closed[-1]; prev = closed[-1 - CFG["LOOKBACK"]:-1]; vprev = closed[-1 - CFG["VOL_N"]:-1]
    hi = max(float(x["h"]) for x in prev); lo = min(float(x["l"]) for x in prev)
    vm = sum(float(x["v"]) for x in vprev) / len(vprev); v = float(c["v"])
    o, cl, h, l = float(c["o"]), float(c["c"]), float(c["h"]), float(c["l"])
    a = atr(closed[:-1])
    info = dict(candle_close_ms=int(c["T"]), close=cl, prev_hi=hi, prev_lo=lo, vol=v, vol_mean20=vm,
                vol_x=round(v / vm, 2) if vm else None, atr=a)
    if vm <= 0: return None, dict(info, reason="zero volume baseline")
    if v < CFG["VOL_MULT"] * vm: return None, dict(info, reason=f"volume {v/vm:.2f}x < {CFG['VOL_MULT']}x")
    if cl > hi and cl > o:
        side, lvl, stop = 1, hi, min(l, hi) - 0.1 * a
    elif cl < lo and cl < o:
        side, lvl, stop = -1, lo, max(h, lo) + 0.1 * a
    else: return None, dict(info, reason="no closed breakout beyond prior 12 candles")
    strength = abs(cl - lvl) / a if a else 0
    return dict(side=side, level=lvl, stop=stop, strength=strength, **info), info

def last_closed_5m(closed):
    """Last fully formed 5m bar from 1m closed candles (T is the 1m close time)."""
    buckets = {}
    for c in closed:
        oT = int(c["T"]) - 60000
        buckets.setdefault(oT // 300000, []).append(c)
    for b in sorted(buckets, reverse=True):
        bars = sorted(buckets[b], key=lambda x: int(x["T"]))
        if len(bars) == 5:
            return dict(o=float(bars[0]["o"]), c=float(bars[-1]["c"]),
                        h=max(float(x["h"]) for x in bars), l=min(float(x["l"]) for x in bars),
                        T=int(bars[-1]["T"]))
    return None

def detect_scalp(cs, now_ms):
    """v4: fewer, stronger 1m breakouts with 5m alignment."""
    closed = sorted([c for c in cs if int(c["T"]) < now_ms], key=lambda c: int(c["T"]))
    need = 40
    if len(closed) < need: return None, dict(reason="incomplete 1m history")
    if any(int(b["T"]) - int(a["T"]) != 60000 for a, b in zip(closed[-need:-1], closed[-need+1:])):
        return None, dict(reason="noncontiguous 1m history")
    look = CFG["SCALP_LOOKBACK"]
    c = closed[-1]; prev = closed[-1-look:-1]
    vmean = sum(float(x["v"]) for x in closed[-21:-1]) / 20
    cl, o, h, l = map(float, (c["c"], c["o"], c["h"], c["l"]))
    a = atr(closed[:-1]); hi = max(float(x["h"]) for x in prev); lo = min(float(x["l"]) for x in prev)
    vx = float(c["v"]) / vmean if vmean else 0
    info = dict(candle_close_ms=int(c["T"]), close=cl, atr=a, vol_x=round(vx, 2), prev_hi=hi, prev_lo=lo)
    if vx < CFG["VOL_MULT"]:
        return None, dict(info, reason=f"1m volume {vx:.2f}x < {CFG['VOL_MULT']}x prior20")
    if a <= 0 or abs(cl - o) < CFG["BODY_ATR"] * a or h <= l:
        return None, dict(info, reason=f"weak candle body <{CFG['BODY_ATR']} ATR")
    if cl > hi and cl > o and (cl - l) / (h - l) >= CFG["CLOSE_LOC"]:
        side, lvl, stop = 1, hi, l - 0.05 * a
    elif cl < lo and cl < o and (h - cl) / (h - l) >= CFG["CLOSE_LOC"]:
        side, lvl, stop = -1, lo, h + 0.05 * a
    else:
        return None, dict(info, reason="no strong 1m breakout beyond preceding 15")
    strength = abs(cl - lvl) / a
    if strength < CFG["MIN_STRENGTH"]:
        return None, dict(info, reason=f"strength {strength:.2f} ATR < {CFG['MIN_STRENGTH']}")
    m5 = last_closed_5m(closed[:-1])
    if not m5:
        return None, dict(info, reason="no complete 5m bar for trend alignment")
    if side == 1 and m5["c"] <= m5["o"]:
        return None, dict(info, reason="5m trend not aligned long")
    if side == -1 and m5["c"] >= m5["o"]:
        return None, dict(info, reason="5m trend not aligned short")
    return dict(info, side=side, level=lvl, stop=stop, strength=strength), info

# ---------------- engine ----------------
class Bot:
    def __init__(s, store, hl):
        s.st = store; s.hl = hl
        s.S = store.load() or new_state()
        s.meta = {}; s.ctx_t = 0
        if "positions" not in s.S:
            old = s.S.pop("position", None)
            s.S["positions"] = [old] if old else []
            entries, completed = [], []
            try:
                with open(store.p("trades.jsonl")) as f:
                    for line in f:
                        r = json.loads(line)
                        if r.get("action") == "open": entries.append(r["t"])
                        if r.get("action") == "trade_complete": completed.append(dict(t=r["t"], win=r["net_pnl"] > 0))
            except FileNotFoundError: pass
            for p in s.S["positions"]:
                if not any(abs(t-p["opened"]) < 1 for t in entries): entries.append(p["opened"])
            s.S["entry_times"] = entries
            s.S["completed_times"] = completed
            store.save(s.S)  # idempotent migration; never alter cash/positions/levels
            s.ev("migration", version=2, positions=len(s.S["positions"]))
        s.S.setdefault("entry_times", [])
        s.S.setdefault("completed_times", [])
        s.S.setdefault("scalp_baseline", {})
        s.S.setdefault("scalp_last_candle", {})
        s.S.setdefault("last_loss_t", None)
    def ev(s, kind, **kw): s.st.log("events.jsonl", dict(event=kind, **kw))
    def roll_day(s):
        d = bog().strftime("%Y-%m-%d")
        if s.S["day"] != d:
            s.S.update(day=d, day_entries=0, day_losses=0); s.ev("day_reset", day=d)

    def equity(s):
        eq = s.S["cash"]
        for p in s.S["positions"]:
            px = p.get("last_px", p["entry"])
            eq += CFG["MARGIN"] * p["size"] / p["size0"] + p["side"] * (px-p["entry"])*p["size"] - px*p["size"]*CFG["TAKER"] + p["funding_open"]
        return eq

    def entries24(s): return sum(t > now()-86400 for t in s.S["entry_times"])

    def fetch_scan(s):
        # Network-only worker: no state mutations; does not block 3-second position monitoring.
        hl = HL(); ctx = hl.ctx()
        coins = [k for _,k in sorted([(float(v["ctx"]["dayNtlVlm"]), k) for k,v in ctx.items()
                 if ":" not in k and not v["meta"].get("isDelisted") and v["ctx"].get("midPx")
                 and float(v["ctx"]["dayNtlVlm"]) >= CFG["MIN_DAY_VLM"]], reverse=True)[:CFG["TOP_N"]]]
        candles = {}
        for coin in coins:
            time.sleep(0.5)
            try: candles[coin] = hl.candles(coin, n=CFG["CANDLE_N"])
            except Exception as e: candles[coin] = str(e)
        return ctx, coins, candles

    # ---- entries ----
    def scan(s, fetched=None):
        s.roll_day()
        ctx, coins, candles = fetched or s.fetch_scan()
        s.meta = ctx; s.ctx_t = now(); nms = int(now()*1000)
        cands = []
        for coin in coins:
            cs = candles[coin]
            if isinstance(cs, str):
                s.st.log("skips.jsonl", dict(coin=coin, reason="candle fetch failed: " + cs)); continue
            sig, info = detect_scalp(cs, nms)
            last_ms = info.get("candle_close_ms")
            if last_ms is None: s.st.log("skips.jsonl", dict(coin=coin, **info)); continue
            if coin not in s.S["scalp_baseline"]:
                s.S["scalp_baseline"][coin] = last_ms; s.ev("baseline", coin=coin, candle_close_ms=last_ms, note="startup candle never traded")
                continue
            if last_ms <= s.S["scalp_baseline"][coin] or s.S["scalp_last_candle"].get(coin) == last_ms: continue
            s.S["scalp_last_candle"][coin] = last_ms
            if nms - last_ms > CFG["SIGNAL_AGE_MS"]:
                s.st.log("skips.jsonl", dict(info, coin=coin, reason=f"stale: closed 1m candle >{CFG['SIGNAL_AGE_MS']//1000}s old")); continue
            if not sig: s.st.log("skips.jsonl", dict(coin=coin, **info)); continue
            cands.append((sig["strength"], coin, sig))
        s.S["last_scan_completed"] = now()
        s.S["last_scan_universe"] = coins
        s.ev("scan", universe=coins, n=len(coins), candidates=[(c, round(x, 2)) for x, c, _ in cands],
             equity=round(s.equity(), 4), entries_24h=s.entries24())
        for _, coin, sig in sorted(cands, reverse=True):
            s.try_enter(coin, sig)

    def gate(s, coin=None):
        if coin and any(p["coin"] == coin for p in s.S["positions"]): return "symbol already open: no duplicate or opposing position"
        if len(s.S["positions"]) >= CFG["MAX_POSITIONS"]: return "3 concurrent positions cap (runners count)"
        if (len(s.S["positions"])+1)*CFG["MARGIN"] > CFG["MAX_INITIAL_MARGIN"]: return "$18 initial margin budget"
        if s.S["cash"] < CFG["MARGIN"] + CFG["MARGIN"]*0.05: return "insufficient cash including entry fee reserve"
        if s.S["halted"]: return "halted: equity kill"
        if s.entries24() >= CFG["MAX_ENTRIES_DAY"]: return "rolling 24h entry cap 10"
        if s.S["day_losses"] >= CFG["MAX_LOSSES_DAY"]: return "2 losses today: entries stopped"
        if s.S.get("last_loss_t") and now() - s.S["last_loss_t"] < CFG["LOSS_COOLDOWN_S"]:
            left = CFG["LOSS_COOLDOWN_S"] - (now() - s.S["last_loss_t"])
            return f"cooldown after loss ({left:.0f}s left)"
        if s.equity() <= CFG["EQUITY_STOP"]: return "equity <= $20"
        return None

    def try_enter(s, coin, sig):
        rec = dict(coin=coin, side="long" if sig["side"] == 1 else "short", signal=sig)
        g = s.gate(coin)
        if g: s.st.log("skips.jsonl", dict(rec, reason=g)); return False
        m = s.meta[coin]; maxlev = float(m["meta"]["maxLeverage"]); fund = float(m["ctx"]["funding"])
        if sig["side"] * fund > CFG["MAX_ADVERSE_FUND_HR"]:
            s.st.log("skips.jsonl", dict(rec, reason=f"adverse funding {fund:.6f}/h")); return False
        try: bs = book_stats(s.hl.book(coin), 0)
        except Exception as e: s.st.log("skips.jsonl", dict(rec, reason=f"book failed {e}")); return False
        if now() - bs["time"] > CFG["STALE_S"]: s.st.log("skips.jsonl", dict(rec, reason="stale book")); return False
        if bs["spread"] > CFG["MAX_SPREAD"]: s.st.log("skips.jsonl", dict(rec, reason=f"spread {bs['spread']:.4%}")); return False
        mid = bs["mid"]
        if (mid - sig["level"]) * sig["side"] <= 0:
            s.st.log("skips.jsonl", dict(rec, reason="price back inside breakout level (not holding)")); return False
        if (mid - sig["close"]) * sig["side"] > CFG["CHASE_ATR"] * sig["atr"]:
            s.st.log("skips.jsonl", dict(rec, reason=f"chasing: >{CFG['CHASE_ATR']} ATR beyond signal close")); return False
        stop = sig["stop"]
        if abs(mid - stop) / mid < 0.0005:
            stop = mid * (1 - sig["side"] * 0.0005)
        pl, why = plan(sig["side"], mid, stop, maxlev, m["meta"]["szDecimals"])
        if not pl: s.st.log("skips.jsonl", dict(rec, reason=why)); return False
        depth = bs["depth_ask"] if sig["side"] == 1 else bs["depth_bid"]
        if depth < CFG["DEPTH_MULT"] * pl["notional"]:
            s.st.log("skips.jsonl", dict(rec, reason=f"thin book depth {depth:.0f}")); return False
        fp = fill_px(sig["side"], True, bs, pl["notional"])
        if fp is None: s.st.log("skips.jsonl", dict(rec, reason="book walk insufficient")); return False
        actuallev=fp*pl["size"]/CFG["MARGIN"]
        # Cost-aware validation at actual proposed simulated entry, adverse 10-minute funding reserve.
        q=pl["size"]; expected_exit=stop*(1-sig["side"]*CFG["EXTRA_SLIP"])
        netrisk=max(0,sig["side"]*(fp-expected_exit)*q)+CFG["TAKER"]*q*(fp+expected_exit)
        netrisk+=q*fp*(0.0005+max(0,sig["side"]*fund)*CFG["SCALP_HOLD_S"]/3600)
        tmp=dict(side=sig["side"],entry=fp,size0=q,entry_fee=fp*q*CFG["TAKER"],funding=0)
        target=px_for_r(tmp,CFG["SCALP_TP_R"])
        if actuallev<CFG["MIN_SCALP_LEV"]:
            s.st.log("skips.jsonl",dict(rec,reason=f"risk-sized leverage {actuallev:.2f}x below preferred15x"));return False
        if actuallev>min(maxlev,CFG["MAX_LEV"]) or netrisk>CFG["MAX_LOSS_USD"]:
            s.st.log("skips.jsonl",dict(rec,reason="actual-fill cost-adjusted risk/leverage exceeds ceiling"));return False
        if abs(target-fp)>4*sig["atr"] or CFG["SCALP_TP_R"]*CFG["MARGIN"]<CFG["RR_MIN"]*netrisk:
            s.st.log("skips.jsonl",dict(rec,reason="net target exceeds4 ATR or net reward/risk below min"));return False
        if s.S["cash"]<CFG["MARGIN"]+fp*q*CFG["TAKER"]:
            s.st.log("skips.jsonl",dict(rec,reason="cash insufficient including entry fee"));return False
        pl["modeled_loss"]=netrisk
        return s.open(coin, sig, pl, fp, stop, maxlev, fund, bs)

    def open(s, coin, sig, pl, fp, stop, maxlev, fund, bs):
        size = pl["size"]; fee = fp * size * CFG["TAKER"]
        pos = dict(strategy=CFG["STRATEGY"],tp_r=CFG["SCALP_TP_R"],trail_arm=CFG["SCALP_TRAIL_ARM"],trail_gap=CFG["SCALP_TRAIL_GAP"],hold_s=CFG["SCALP_HOLD_S"],coin=coin, side=sig["side"], entry=fp, size0=size, size=size, entry_fee=fee, funding=0.0,
                   funding_open=0.0, lev=round(fp * size / CFG["MARGIN"], 3), maxlev=maxlev, opened=now(),
                   liq=liq_price(sig["side"], fp, size, CFG["MARGIN"], maxlev), struct_stop=stop,
                   peak_r=0.0, trail_armed=False, tp_done=False, realized_slices=[], last_px=fp,
                   last_fund_hr=int(now() // 3600), last_ok=now(), id=f"{coin}-{int(now())}",
                   modeled_max_loss=pl["modeled_loss"])
        sp = px_for_r(pos, CFG["STOP_R"])
        # never wider than -10%; structural stop used only if tighter
        pos["stop_px"] = max(sp, stop) if sig["side"] == 1 else min(sp, stop)
        pos["tp_px"] = px_for_r(pos, pos["tp_r"])
        s.S["cash"] -= CFG["MARGIN"] + fee
        s.S["positions"].append(pos); s.S["day_entries"] += 1; s.S["entry_times"].append(pos["opened"])
        reason = (f"{'LONG' if sig['side']==1 else 'SHORT'} {coin}: closed 1m candle {sig['close']} broke prior-5 "
                  f"{'high' if sig['side']==1 else 'low'} {sig['level']} on {sig['vol_x']}x vol (>=4x prior20 mean); "
                  f"risk-sized leverage {fp*size/CFG['MARGIN']:.2f}x (venue max {maxlev:g}x), modeled loss ${pl['modeled_loss']:.3f}; still holding beyond level; spread {bs['spread']:.4%}; funding {fund:.6f}/h; strength {sig['strength']:.2f} ATR")
        s.st.log("trades.jsonl", dict(action="open", strategy=pos["strategy"], id=pos["id"], coin=coin, side=sig["side"], entry=fp, size=size,
                 notional=fp * size, lev=pos["lev"], fee=fee, stop_px=pos["stop_px"], tp_px=pos["tp_px"],
                 liq=pos["liq"], modeled_max_loss=pl["modeled_loss"], reason=reason))
        s.st.log("alerts.jsonl", dict(alert="ENTRY", text=reason))
        return True

    # ---- position mgmt ----
    def accrue_funding(s, pos, fund_rate, mark):
        hr = int(now() // 3600)
        while pos["last_fund_hr"] < hr:
            pos["last_fund_hr"] += 1
            amt = -pos["side"] * fund_rate * mark * pos["size"]
            pos["funding"] += amt; pos["funding_open"] += amt
            s.ev("funding", coin=pos["coin"], rate=fund_rate, usd=amt)

    def close_slice(s, pos, frac_of_open, bs, why, trig_px, px_override=None):
        size = pos["size"] if frac_of_open >= 1 else round_sz(pos["size"] * frac_of_open, s.meta.get(pos["coin"], {}).get("meta", {}).get("szDecimals", 4))
        px = px_override if px_override is not None else (fill_px(pos["side"], False, bs, size * bs["mid"]) or (bs["bid"] if pos["side"] == 1 else bs["ask"]))
        gross = pos["side"] * (px - pos["entry"]) * size; fee = px * size * CFG["TAKER"]
        margin_back = CFG["MARGIN"] * size / pos["size0"]
        fshare = pos["funding_open"] * size / pos["size"]; pos["funding_open"] -= fshare
        pos["size"] = round(pos["size"] - size, 10)
        s.S["cash"] += margin_back + gross - fee + fshare
        sl = dict(execution_unverified=size * px < CFG["MIN_NOTIONAL"], size=size, px=px, gross=gross, fee=fee, why=why, trig_px=trig_px,
                  gap=(px - trig_px) * pos["side"] if trig_px else None)
        pos["realized_slices"].append(sl)
        s.st.log("trades.jsonl", dict(action="close_slice", id=pos["id"], **sl, remaining=pos["size"]))
        if pos["size"] <= 0: s.finish(pos)

    def finish(s, pos):
        net = sum(x["gross"] - x["fee"] for x in pos["realized_slices"]) - pos["entry_fee"] + pos["funding"]
        win = net > 0
        s.S["realized"] += net; s.S["trades"] += 1
        if win: s.S["wins"] += 1; s.S["gross_win"] += net
        else: s.S["losses"] += 1; s.S["gross_loss"] += net; s.S["day_losses"] += 1; s.S["last_loss_t"] = now()
        rec = dict(action="trade_complete", strategy=pos.get("strategy","momentum_v2"), id=pos["id"], coin=pos["coin"], net_pnl=net, ret_on_margin=net / CFG["MARGIN"],
                   result="win" if win else "loss", slices=len(pos["realized_slices"]), funding=pos["funding"],
                   entry_fee=pos["entry_fee"], peak_r=pos["peak_r"], execution_unverified=pos.get("execution_unverified", []), equity=s.S["cash"])
        s.st.log("trades.jsonl", rec); s.st.log("alerts.jsonl", dict(alert="EXIT", **rec))
        s.S["positions"].remove(pos)
        s.S["completed_times"].append(dict(t=now(), win=win))
        if s.equity() < CFG["EQUITY_STOP"]: s.S["halted"] = True; s.ev("halt", reason="equity < $20")

    def manage(s, bs, fund_rate, mark, pos=None):
        pos = pos or s.S["positions"][0]; side = pos["side"]
        s.accrue_funding(pos, fund_rate, mark)
        exit_px = bs["bid"] if side == 1 else bs["ask"]       # conservative exit side
        pos["last_px"] = exit_px; pos["last_ok"] = now()
        r = r_full(pos, exit_px); pos["peak_r"] = max(pos["peak_r"], r)
        if pos["peak_r"] >= pos.get("trail_arm",CFG["TRAIL_ARM"]) and not pos["trail_armed"]:
            pos["trail_armed"] = True; s.ev("trail_armed", id=pos["id"], r=r)
        crossed = lambda lvl: (exit_px - lvl) * side <= 0
        if s.S["halted"] or s.equity() < CFG["EQUITY_STOP"]:
            s.close_slice(pos, 1, bs, "equity kill <$20 flatten", None); s.S["halted"] = True; return
        if crossed(pos["stop_px"]):
            s.close_slice(pos, 1, bs, "structural/net-risk stop", pos["stop_px"]); return
        cap = min(float(pos.get("modeled_max_loss") or CFG["MAX_LOSS_USD"]), CFG["MAX_LOSS_USD"])
        if (not pos.get("tp_done")) and r <= -cap / CFG["MARGIN"]:
            s.close_slice(pos, 1, bs, f"hard dollar stop ${cap:.2f}", px_for_r(pos, -cap / CFG["MARGIN"])); return
        if pos["trail_armed"]:
            tpx = px_for_r(pos, pos["peak_r"] - pos.get("trail_gap",CFG["TRAIL_GAP"]))
            if crossed(tpx):
                s.close_slice(pos, 1, bs, f"trail: peak {pos['peak_r']:.1%} gap {pos.get('trail_gap',CFG['TRAIL_GAP']):.1%}", tpx); return
        if now()-pos["opened"] >= pos.get("hold_s",CFG["MAIN_MAX_HOLD_S"]) and not (pos["tp_done"] and pos["trail_armed"]):
            s.close_slice(pos, 1, bs, f"time_exit: main maximum {pos.get('hold_s',CFG['MAIN_MAX_HOLD_S'])}s", exit_px); return
        if pos.get("strategy") == "scalp_momentum_v4" and (not pos["tp_done"]) and (not pos["trail_armed"]) and now()-pos["opened"] >= CFG["SCRATCH_S"] and r < CFG["SCRATCH_MIN_R"]:
            s.close_slice(pos, 1, bs, "scratch: no follow-through in 2 minutes", exit_px); return
        if not pos["tp_done"] and r >= pos.get("tp_r",CFG["TP_R"]):
            # approved contract: always 90% off at +40%, keep 10% runner. Sub-$10 legs are PAPER-modelled and flagged
            # execution_unverified (docs state a $10 min order value; whether it binds reduce-only closes is unverified).
            runner_ntl = pos["size0"] * (1 - CFG["TP_FRAC"]) * exit_px
            tp_ntl = pos["size0"] * CFG["TP_FRAC"] * exit_px
            flags = [n for n, v in (("tp_slice", tp_ntl), ("runner", runner_ntl)) if v < CFG["MIN_NOTIONAL"]]
            if flags:
                pos["execution_unverified"] = sorted(set(pos.get("execution_unverified", []) + flags))
                s.ev("execution_unverified", id=pos["id"], legs=flags, tp_notional=tp_ntl, runner_notional=runner_ntl,
                     note="paper only: leg below $10 documented min order value; live executability not verified")
            pos["tp_done"] = True
            s.close_slice(pos, CFG["TP_FRAC"], bs, f"TP90 at +{pos.get('tp_r',CFG['TP_R']):.0%} margin", pos["tp_px"])

    def synth_book(s, px, t=None):
        return book_stats({"levels": [[{"px": str(px), "sz": "1000000"}], [{"px": str(px), "sz": "1000000"}]], "time": (t or now()) * 1000}, 0)

    def catchup_position(s, pos):
        """If monitoring was down, walk missed 1m candles and fill stops at the stop (or the gap open)."""
        last = pos.get("last_ok", pos["opened"])
        gap = now() - last
        if gap < 45: return False
        s.ev("catchup_start", id=pos["id"], gap_s=round(gap, 1), coin=pos["coin"])
        try:
            cs = s.hl.candles(pos["coin"], n=max(40, int(gap / 60) + 8))
        except Exception as e:
            s.ev("catchup_failed", id=pos["id"], err=str(e)[:200]); return False
        closed = sorted([c for c in cs if last * 1000 < int(c["T"]) <= now() * 1000], key=lambda c: int(c["T"]))
        cap = min(float(pos.get("modeled_max_loss") or CFG["MAX_LOSS_USD"]), CFG["MAX_LOSS_USD"])
        dollar_px = px_for_r(pos, -cap / CFG["MARGIN"])
        hold_s = pos.get("hold_s", CFG["MAIN_MAX_HOLD_S"])
        for c in closed:
            o, h, l, cl = map(float, (c["o"], c["h"], c["l"], c["c"]))
            bar_t = int(c["T"]) / 1000
            fill = why = trig = None
            stop = pos["stop_px"]
            if pos["side"] == 1:
                if o <= stop: fill, why, trig = o, "catchup gap-through stop", stop
                elif l <= stop: fill, why, trig = stop, "catchup stop in-bar", stop
            else:
                if o >= stop: fill, why, trig = o, "catchup gap-through stop", stop
                elif h >= stop: fill, why, trig = stop, "catchup stop in-bar", stop
            if fill is None and not pos.get("tp_done"):
                if pos["side"] == 1:
                    if o <= dollar_px: fill, why, trig = o, "catchup gap-through hard dollar stop", dollar_px
                    elif l <= dollar_px: fill, why, trig = dollar_px, "catchup hard dollar stop in-bar", dollar_px
                else:
                    if o >= dollar_px: fill, why, trig = o, "catchup gap-through hard dollar stop", dollar_px
                    elif h >= dollar_px: fill, why, trig = dollar_px, "catchup hard dollar stop in-bar", dollar_px
            if fill is None:
                if bar_t - pos["opened"] >= hold_s and not (pos.get("tp_done") and pos.get("trail_armed")):
                    fill, why, trig = cl, f"catchup time_exit: main maximum {hold_s}s", cl
                elif pos.get("strategy") == "scalp_momentum_v4" and (not pos.get("tp_done")) and (not pos.get("trail_armed")) and bar_t - pos["opened"] >= CFG["SCRATCH_S"] and r_full(pos, cl) < CFG["SCRATCH_MIN_R"]:
                    fill, why, trig = cl, "catchup scratch: no follow-through in 2 minutes", cl
            if fill is not None:
                cctx = s.meta.get(pos["coin"], {}).get("ctx", {})
                s.accrue_funding(pos, float(cctx.get("funding", 0) or 0), fill)
                s.close_slice(pos, 1, s.synth_book(fill, bar_t), why, trig, px_override=fill)
                s.ev("catchup_fill", id=pos.get("id"), why=why, fill=fill, trig=trig, bar_t=bar_t)
                return True
            pos["last_px"] = cl; pos["last_ok"] = bar_t
            pos["peak_r"] = max(pos["peak_r"], r_full(pos, cl))
        s.ev("catchup_done", id=pos["id"], bars=len(closed), still_open=True)
        return False

    def tick_position(s, pos):
        try:
            if now() - pos.get("last_ok", pos["opened"]) >= 45:
                if s.catchup_position(pos):
                    return
            bs = book_stats(s.hl.book(pos["coin"]), 0)
            if now() - bs["time"] > CFG["STALE_S"]: raise RuntimeError("stale book")
            c = s.meta.get(pos["coin"], {}).get("ctx", {})
            s.manage(bs, float(c.get("funding", 0)), float(c.get("markPx", bs["mid"])), pos)
        except Exception as e:
            age = now() - pos.get("last_ok", now())
            s.ev("position_data_error", coin=pos["coin"], err=str(e)[:200], age_s=round(age, 1))
            if age > 60: s.st.log("alerts.jsonl", dict(alert="STALE_POSITION_DATA", coin=pos["coin"], age_s=age))

    def heartbeat(s, extra=None):
        completed = [x for x in s.S["completed_times"] if x["t"] > now()-86400]
        hb = dict(strategy=CFG["STRATEGY"],ts=iso(), t=now(), pid=os.getpid(), equity=round(s.equity(),6), cash=s.S["cash"], realized=s.S["realized"],
                  trades=s.S["trades"], wins=s.S["wins"], losses=s.S["losses"], entries_24h=s.entries24(),
                  completed_24h=len(completed), win_rate_24h=sum(x["win"] for x in completed)/len(completed) if completed else None,
                  last_scan_completed=iso(s.S["last_scan_completed"]) if s.S.get("last_scan_completed") else None,
                  scan_age_s=round(now()-s.S["last_scan_completed"],1) if s.S.get("last_scan_completed") else None,
                  last_scan_universe=s.S.get("last_scan_universe", []),
                  day_losses=s.S["day_losses"], halted=s.S["halted"], entry_hours="24/7", reserved_initial_margin=6*len(s.S["positions"]),
                  positions=[dict(p, r_full=r_full(p,p["last_px"])) for p in s.S["positions"]], **(extra or {}))
        tmp = s.st.p("heartbeat.json.tmp")
        with open(tmp,"w") as f: json.dump(hb,f,indent=1)
        os.replace(tmp,s.st.p("heartbeat.json"))

    def run(s, once=False):
        stop = {"v": False}
        signal.signal(signal.SIGTERM, lambda *a: stop.update(v=True))
        signal.signal(signal.SIGINT, lambda *a: stop.update(v=True))
        s.ev("start", pid=os.getpid(), cfg=CFG, resumed_positions=len(s.S["positions"]), strategy=CFG["STRATEGY"])
        for pos in list(s.S["positions"]):
            try: s.catchup_position(pos)
            except Exception as e: s.ev("catchup_error", id=pos.get("id"), err=str(e)[:200])
        last_scan, errs = 0, 0
        pool = ThreadPoolExecutor(max_workers=1); future = None
        while not stop["v"]:
            try:
                bar = int((now() - 5) // 60)          # new scalp scan once per closed 1m bar, 5s after close
                s.roll_day()
                if bar != last_scan and future is None:
                    last_scan = bar; future = pool.submit(s.fetch_scan)
                if future is not None and future.done():
                    done = future; future = None
                    s.scan(done.result())
                if s.S["positions"] and now() - s.ctx_t > 60:
                    s.meta = s.hl.ctx(); s.ctx_t = now()
                for pos in list(s.S["positions"]): s.tick_position(pos)
                s.store_ok(); errs = 0
                s.heartbeat(dict(last_scan_bar_utc=datetime.fromtimestamp(last_scan*60, timezone.utc).isoformat(), status="ok"))
            except Exception as e:
                errs += 1
                s.ev("error", err=f"{type(e).__name__}: {e}"[:300], n=errs, tb=traceback.format_exc()[-800:])
                s.heartbeat(dict(status=f"error x{errs}: {e}"[:200]))
                if errs >= 20: s.ev("fatal", reason="20 consecutive errors; exiting for supervisor"); sys.exit(3)
                time.sleep(min(60, 2 ** errs))
            if once: break
            time.sleep(CFG["POS_POLL"] if s.S["positions"] else 5)
        pool.shutdown(wait=True)
        s.store_ok(); s.ev("stop", pid=os.getpid())
    def store_ok(s): s.st.save(s.S)

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--data", default=DATA); ap.add_argument("--once", action="store_true")
    a = ap.parse_args(); os.makedirs(a.data, exist_ok=True)
    lk = open(os.path.join(a.data, "bot.lock"), "w")
    try: fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError: print("another instance holds the lock; exiting"); sys.exit(2)
    lk.write(str(os.getpid())); lk.flush()
    Bot(Store(a.data), HL()).run(once=a.once)

if __name__ == "__main__": main()
