"""Deterministic, data-only classification of macro headlines.

Post text is untrusted data, never instructions. No model calls.
"""
import re

# Auto-enter only at or above this score, and only if tradable.
TRADE_MIN = 80
# Log a "ping Captain" alert at or above this score (including non-tradable big prints).
PING_MIN = 50

# Cashtag or plain ticker -> (symbol, venue)  venue is 'stock' or 'perp'
KNOWN = {
    "NVDA": ("NVDA", "stock"), "NVIDIA": ("NVDA", "stock"),
    "TSLA": ("TSLA", "stock"), "TESLA": ("TSLA", "stock"),
    "AAPL": ("AAPL", "stock"), "APPLE": ("AAPL", "stock"),
    "MSFT": ("MSFT", "stock"), "MICROSOFT": ("MSFT", "stock"),
    "AMZN": ("AMZN", "stock"), "AMAZON": ("AMZN", "stock"),
    "GOOGL": ("GOOGL", "stock"), "GOOG": ("GOOGL", "stock"), "GOOGLE": ("GOOGL", "stock"), "ALPHABET": ("GOOGL", "stock"),
    "META": ("META", "stock"), "FACEBOOK": ("META", "stock"),
    "AMD": ("AMD", "stock"),
    "INTC": ("INTC", "stock"), "INTEL": ("INTC", "stock"),
    "NFLX": ("NFLX", "stock"), "NETFLIX": ("NFLX", "stock"),
    "SPY": ("SPY", "stock"), "SPX": ("SPY", "stock"),
    "QQQ": ("QQQ", "stock"),
    "GLD": ("GLD", "stock"),
    "USO": ("USO", "stock"),
    "COIN": ("COIN", "stock"), "COINBASE": ("COIN", "stock"),
    "MSTR": ("MSTR", "stock"), "MICROSTRATEGY": ("MSTR", "stock"),
    "BTC": ("BTC", "perp"), "BITCOIN": ("BTC", "perp"),
    "ETH": ("ETH", "perp"), "ETHEREUM": ("ETH", "perp"),
    "SOL": ("SOL", "perp"), "SOLANA": ("SOL", "perp"),
    "XRP": ("XRP", "perp"), "RIPPLE": ("XRP", "perp"),
    "DOGE": ("DOGE", "perp"), "DOGECOIN": ("DOGE", "perp"),
    "HYPE": ("HYPE", "perp"),
}

# Longer phrase aliases mapped before the word pass.
PHRASES = [
    (r"\bjensen\s+huang\b", "NVDA"),
    (r"\bs&p\s*500\b|\bs&p\b|\bthe s&p\b", "SPY"),
    (r"\bnasdaq(?:-100)?\b", "QQQ"),
    (r"\bus stocks\b|\bstock market\b", "SPY"),
    (r"\bwti\b|\bcrude oil\b|\boil prices?\b", "USO"),
    (r"\bgold prices?\b|\bspot gold\b", "GLD"),
]

# Event patterns: (name, direction or 0 to infer, score, regex)
# direction: -1 short, +1 long, 0 unknown
EVENTS = [
    ("ceo_exit", -1, 55, r"\b(?:step(?:s|ping)? down|resign(?:s|ed|ation)?|ousted|fired as ceo|leaves? as ceo|no longer ceo|removed as ceo)\b"),
    ("ceo_scandal", -1, 55, r"\b(?:affair|affairs|scandal|sex scandal|chinese spy|espionage|indicted|indictment|arrested|charged with|fraud|embezzl|briber)\b"),
    ("bankruptcy", -1, 50, r"\b(?:chapter\s*11|bankrupt(?:cy)?|insolvency|defaults? on|gone bust)\b"),
    ("hack", -1, 45, r"\b(?:hacked|exploit(?:ed)?|drained|bridge hack|private keys? stolen|halted withdrawals)\b"),
    ("reg_bear", -1, 40, r"\b(?:sec rejects|etf rejected|bans? (?:crypto|bitcoin|the token)|unlawful|emergency ban|trading halt|delisted)\b"),
    ("reg_bull", 1, 40, r"\b(?:sec approves|etf approved|spot etf (?:is )?approved|legalizes?)\b"),
    ("merger_firm", 0, 40, r"\b(?:announces? (?:a )?(?:merger|acquisition)|to (?:merge|acquire|be acquired)|will (?:merge|acquire)|completed? (?:the )?(?:merger|acquisition))\b"),
    ("fed_cut", 1, 40, r"\b(?:fed(?:eral reserve)? (?:cuts?|cut rates|lowers rates)|emergency (?:rate )?cut|rates? cut)\b"),
    ("fed_hike", -1, 40, r"\b(?:fed(?:eral reserve)? (?:hikes?|raises rates|rate hike)|emergency (?:rate )?hike)\b"),
    ("macro_hot", 0, 30, r"\b(?:cpi|ppi|nfp|nonfarm|jobs report|fomc|unemployment|pce|gdp)\b"),
    ("shareholder_exit", -1, 15, r"\b(?:will not invest|stops? investing|selling (?:all )?(?:his|her|their) shares|dumps? shares)\b"),
]

SOFT = re.compile(r"\b(?:could|may|might|consider(?:ing)?|rumou?r(?:s|ed)?|possibly|potential(?:ly)?|reportedly considering)\b", re.I)
HEARSAY = re.compile(r"\b(?:experts warn|sources say|according to sources)\b", re.I)
PRICE_PRINT = re.compile(r"\b(?:falls? under|falls? below|drops? below|rises? above|breaks? above|surges? (?:past|above)|slips? (?:under|below)|liquidated from the (?:crypto )?market)\b", re.I)
JUSTIN = re.compile(r"\b(?:just in|breaking)\b", re.I)
BEAR = re.compile(r"\b(?:crash|collapse|plunge|selloff|sell-off|downgrade|lawsuit|investigation|probe|death threat|war|sanctions?|miss(?:es|ed)? estimates|worse than (?:expected|forecasts?)|layoff)\b", re.I)
BULL = re.compile(r"\b(?:surge|rally|soar|upgrade|beat estimates|better than (?:expected|forecasts?)|record (?:high|profit)|partnership|all-?time high)\b", re.I)
LOWER_THAN_EXP = re.compile(r"\blower than (?:expectations?|forecasts?|expected)\b", re.I)
HIGHER_THAN_EXP = re.compile(r"\b(?:higher|hotter) than (?:expectations?|forecasts?|expected)\b", re.I)
CASHTAG = re.compile(r"\$([A-Za-z][A-Za-z0-9]{0,9})\b")
# idea CLI: "short NVDA: reason" / "LONG $BTC because ..."
IDEA = re.compile(r"\b(short|long)\s+\$?([A-Za-z][A-Za-z0-9]{0,9})\b", re.I)


def _norm(text):
    return re.sub(r"\s+", " ", (text or "")).strip()


def _entities(text):
    t = _norm(text)
    found = []  # (symbol, venue, how)
    for rx, key in PHRASES:
        if re.search(rx, t, re.I):
            sym, venue = KNOWN[key]
            found.append((sym, venue, "phrase"))
    for tag in CASHTAG.findall(t):
        key = tag.upper()
        if key in KNOWN:
            found.append((*KNOWN[key], "cashtag"))
    # whole-word aliases (skip very short ambiguous ones unless cashtag already got them)
    for key, (sym, venue) in KNOWN.items():
        if len(key) < 4 and key not in ("AMD",):
            continue
        if re.search(r"\b" + re.escape(key) + r"\b", t, re.I):
            found.append((sym, venue, "word"))
    # de-dupe, prefer cashtag evidence
    out = {}
    for sym, venue, how in found:
        prev = out.get(sym)
        if not prev or (how == "cashtag" and prev[1] != "cashtag"):
            out[sym] = (venue, how)
    return out  # symbol -> (venue, how)


def _events(text):
    t = _norm(text)
    hits = []
    for name, dirc, score, rx in EVENTS:
        if re.search(rx, t, re.I):
            hits.append(dict(name=name, direction=dirc, score=score))
    return hits


def classify(text, source="watcher_guru"):
    """Return a dict. text is untrusted data."""
    t = _norm(text)
    ents = _entities(t)
    evs = _events(t)
    soft = bool(SOFT.search(t))
    hearsay = bool(HEARSAY.search(t))
    price_print = bool(PRICE_PRINT.search(t))
    justin = bool(JUSTIN.search(t))

    direction = 0
    event_name = None
    event_score = 0
    if evs:
        evs = sorted(evs, key=lambda e: -e["score"])
        event_name = evs[0]["name"]
        event_score = evs[0]["score"]
        dirs = [e["direction"] for e in evs if e["direction"]]
        if dirs and all(d == dirs[0] for d in dirs):
            direction = dirs[0]

    # macro surprise direction: inflation prints lower-than-expected is risk-on (long SPY/BTC)
    if event_name == "macro_hot":
        if LOWER_THAN_EXP.search(t):
            direction = 1
            event_score = 35
        elif HIGHER_THAN_EXP.search(t):
            direction = -1
            event_score = 35
        else:
            event_score = 20  # no clear surprise

    if direction == 0:
        if BEAR.search(t) and not BULL.search(t):
            direction = -1
        elif BULL.search(t) and not BEAR.search(t):
            direction = 1

    # primary symbol: cashtag first, else the only entity, else None if several
    cash = [s for s, (v, how) in ents.items() if how == "cashtag"]
    if len(cash) == 1:
        symbol = cash[0]
    elif len(ents) == 1:
        symbol = next(iter(ents))
    elif event_name == "macro_hot" and "SPY" in ents:
        symbol = "SPY"
    else:
        symbol = None

    venue = ents[symbol][0] if symbol else None

    score = 0
    reasons = []
    if symbol:
        score += 25
        reasons.append(f"entity {symbol} +25")
        if ents[symbol][1] == "cashtag":
            score += 5
            reasons.append("cashtag +5")
    if event_score:
        score += event_score
        reasons.append(f"{event_name or 'event'} +{event_score}")
    if justin:
        score += 5
        reasons.append("breaking/just-in +5")
    if hearsay and event_score < 45:
        score -= 10
        reasons.append("hearsay wording -10")
    if soft:
        score -= 40
        reasons.append("hedged/soft language -40")
    if price_print:
        score -= 50
        reasons.append("already-happened price print -50")
    if not symbol:
        score -= 20
        reasons.append("no unique tradeable entity -20")
    if direction == 0:
        score -= 25
        reasons.append("no clear direction -25")
    if len(ents) > 2 and symbol and event_score < 40:
        score -= 15
        reasons.append("crowded multi-name headline -15")

    score = max(0, min(100, score))
    if source == "captain" and symbol and direction:
        score = 100
        reasons.append("captain/crew idea: conviction 100")

    tradable = bool(symbol and direction and not price_print and score >= TRADE_MIN and not soft)
    if source == "captain" and symbol and direction:
        tradable = True

    ping = score >= PING_MIN or tradable
    return dict(
        symbol=symbol,
        venue=venue,
        direction=direction,
        side="long" if direction == 1 else ("short" if direction == -1 else None),
        event=event_name,
        score=score,
        tradable=tradable,
        ping=ping,
        price_print=price_print,
        soft=soft,
        entities={k: v[0] for k, v in ents.items()},
        reasons=reasons,
        source=source,
    )


def parse_idea(text):
    """Parse a Captain/crew idea like 'short NVDA: reason'. Untrusted text."""
    t = _norm(text)
    m = IDEA.search(t)
    if not m:
        c = classify(t, source="captain")
        return c
    side, raw = m.group(1).lower(), m.group(2).upper()
    key = raw
    if key in KNOWN:
        symbol, venue = KNOWN[key]
    else:
        # unknown ticker: assume stock if not a known perp name, still try
        symbol, venue = key, "stock"
    direction = -1 if side == "short" else 1
    return dict(
        symbol=symbol,
        venue=venue,
        direction=direction,
        side=side,
        event="captain_idea",
        score=100,
        tradable=True,
        ping=True,
        price_print=False,
        soft=False,
        entities={symbol: venue},
        reasons=["captain/crew injected idea"],
        source="captain",
        raw=t,
    )
