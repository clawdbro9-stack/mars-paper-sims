"""Public paper quotes: Yahoo Finance for US stocks, Hyperliquid info for perps."""
import json, time, urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1m&range=1d"
HL_API = "https://api.hyperliquid.xyz/info"
UA = "PublicPaperResearch/1.0"
NY = ZoneInfo("America/New_York")
HTTP_TIMEOUT = 10
MAX_QUOTE_AGE = 120  # seconds; stocks during RTH and crypto always


def _get(url, data=None):
    req = urllib.request.Request(
        url,
        data=None if data is None else json.dumps(data).encode(),
        headers={"User-Agent": UA, "Content-Type": "application/json"} if data else {"User-Agent": UA},
        method="POST" if data is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return json.loads(r.read().decode())


def session_now(ts=None):
    t = datetime.fromtimestamp(ts or time.time(), NY)
    if t.weekday() >= 5:
        return "weekend"
    minutes = t.hour * 60 + t.minute
    if minutes < 4 * 60:
        return "closed"
    if minutes < 9 * 60 + 30:
        return "pre"
    if minutes < 16 * 60:
        return "rth"
    if minutes < 20 * 60:
        return "post"
    return "closed"


def stock_quote(symbol):
    d = _get(YAHOO.format(sym=symbol))
    err = (d.get("chart") or {}).get("error")
    if err:
        raise RuntimeError(f"yahoo error {err}")
    res = (d.get("chart") or {}).get("result") or []
    if not res:
        raise RuntimeError("yahoo empty result")
    meta = res[0]["meta"]
    px = meta.get("regularMarketPrice")
    ts = meta.get("regularMarketTime")
    if not px or not ts:
        raise RuntimeError("yahoo missing price")
    sess = session_now()
    # if Yahoo carries currentTradingPeriod, prefer that for session label
    ctp = meta.get("currentTradingPeriod") or {}
    now = time.time()
    if ctp:
        regular = ctp.get("regular") or {}
        if regular.get("start") and regular.get("end"):
            if regular["start"] <= now < regular["end"]:
                sess = "rth"
            elif now < regular["start"]:
                pre = ctp.get("pre") or {}
                sess = "pre" if pre.get("start") and pre["start"] <= now else "closed"
            else:
                post = ctp.get("post") or {}
                sess = "post" if post.get("end") and now < post["end"] else "closed"
    return dict(
        symbol=symbol, venue="stock", px=float(px), t=float(ts),
        source="yahoo_chart", session=sess, age_s=max(0.0, time.time() - float(ts)),
        currency=meta.get("currency", "USD"),
    )


def hl_meta():
    m, c = _get(HL_API, {"type": "metaAndAssetCtxs"})
    out = {}
    for a, x in zip(m["universe"], c):
        if a.get("isDelisted") or ":" in a["name"]:
            continue
        mid = x.get("midPx") or x.get("markPx")
        if not mid:
            continue
        out[a["name"]] = dict(
            symbol=a["name"], venue="perp", px=float(mid), t=time.time(),
            source="hl_metaAndAssetCtxs", session="24/7", age_s=0.0,
            mark=float(x.get("markPx") or mid), funding=float(x.get("funding") or 0),
            max_lev=float(a.get("maxLeverage") or 1), sz_decimals=int(a.get("szDecimals") or 4),
            day_vlm=float(x.get("dayNtlVlm") or 0),
        )
    return out


def quote(symbol, venue, hl_cache=None):
    if venue == "perp":
        cache = hl_cache if hl_cache is not None else hl_meta()
        if symbol not in cache:
            raise RuntimeError(f"no hl market for {symbol}")
        q = dict(cache[symbol])
        q["age_s"] = 0.0
        return q
    return stock_quote(symbol)


def usable_for_entry(q):
    if q["venue"] == "stock":
        if q["session"] != "rth":
            return False, f"US market {q['session']}, stock entries only in regular hours"
        if q["age_s"] > MAX_QUOTE_AGE:
            return False, f"stale stock quote {q['age_s']:.0f}s"
        return True, None
    if q["age_s"] > MAX_QUOTE_AGE:
        return False, f"stale perp quote {q['age_s']:.0f}s"
    return True, None
