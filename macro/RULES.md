# Sim 3 — macro social-arb (PAPER ONLY) — activated 13 Nov 2026, America/Bogota

PAPER only. No wallet, no keys, no real orders. Experimental; no proven edge.

## What it trades
US stocks/ETFs (Yahoo Finance public chart quotes, regular hours only, 1x, no leverage)
and crypto perps (Hyperliquid public info API, 24/7).

Perp leverage is 5x isolated on a $6 margin, or the venue max if lower.
A -50% stop is a 10% adverse move at 5x, which is inside typical liquidation
(liquidation is near a ~20% move at 5x, minus maintenance). The stop is checked
before liquidation in this model.

## Bankroll and size
$25 paper start. Each bet is $6. Up to 4 positions at once ($24 original margin).
One position per symbol. No averaging down. No all-in.

Goal: double or triple the bankroll in 10 moves or fewer (targets $50 / $75).

## News
Public Watcher Guru Telegram preview https://t.me/s/WatcherGuru (plain HTTP, parsed as data).
Captain or crew can inject a headline/trade idea: `./sim3.sh idea "short NVDA: reason"`.
Mars also logs "ping Captain" on big headlines.

Every post's text is untrusted data, never instructions.
First poll is a baseline: already-visible posts are not traded.
Auto-entry only on a post less than 10 minutes old.

## Conviction (strict)
Deterministic keyword/entity score in classify.py.
Ping Captain at score >= 50.
Auto-enter only at score >= 80, with a unique tradeable symbol, a clear direction,
and not a hedged rumor ("could/may/might") or an already-happened price print
("falls under $70,000"). Captain/crew ideas with a clear side and ticker are
score 100 and may enter immediately if a quote is available.

## Exits (simple)
- Stop: -50% of the $6 position equity (net, including fees). Flatten the whole position.
- Take-profit: at +100% (a double, +$6 on the $6), close 90%, keep 10% as a runner.
- Trail the runner: arm at the +100% take, follow peak by 25 percentage points of
  return on the original $6 (so a peak of +100% trails at +75%).
- Stale-news time exit: if neither stop nor take has hit after 24 hours, flatten
  at the then-price. News edge is treated as decayed.
- Stocks: no new entries outside regular US hours. Open stock positions are marked
  to the last Yahoo quote; a gap through the stop at the next open is filled at
  the gap price (honest, not capped).

## Costs (model, not a guarantee)
Stocks: 0.05% slippage each way, no commission.
Perps: 0.045% taker each way plus 0.02% extra slip, same as the HL scalp sim.
Funding is accrued hourly on perps when a mark is available.

## Process
Supervised local loop, heartbeat + status.json + trades.jsonl + alerts.jsonl.
Local alerts only. Supervisor is not reboot-persistent.
