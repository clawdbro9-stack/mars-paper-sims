# scalp_momentum_v4 — 13 Nov 2026, America/Bogota
PAPER ONLY. Experimental; no proven edge. High leverage, 1m momentum scalps, $6 bets kept.

v3 final (archived in data/archive_v3/): equity $20.292675, realized -$4.707325, 13 trades, 3W/10L.
Fresh $25 paper bankroll for v4; counters start at zero. v3 ledger was not edited.

## Why v3 was rethought
10 losses vs 3 wins. Last 24h of v3: 0W/3L. Weak 1m breakouts (body 0.5 ATR, vol 2x, only 5 prior candles)
were taken and often stopped in under a minute. The worst loss, an XRP short, was modeled at $0.45 but
lost $2.30 after an ~8h monitoring gap (last tick 12 Nov 23:44, resume 13 Nov 07:27 Bogota) filled far
through the stop. Wins were small (+12% margin target).

## Entries (far fewer, higher quality)
Closed 1m candle breaking the preceding 15-candle high/low; volume >=4x prior-20 mean.
Candle body >=1.0 prior-14 ATR; close in the top/bottom 15% of the range.
Breakout strength (close beyond the level) >=0.75 ATR.
Last fully formed 5m bar (from the 1m history) must be in the same direction.
Live price still holding beyond the breakout; reject chasing >0.20 ATR past the signal close.
Signal age <=20s; version-specific startup baseline never traded.
Same style universe but tighter: native markets with >=$50M day volume, top 8, spread <=0.02%,
depth >=5x notional within 0.2%, book <=20s old. Adverse funding guard unchanged.

## Risk and leverage (kept high, losses capped)
Prefer 15-30x, bounded by the venue max; SKIP if risk-sized leverage is below 15x.
Size from the structural stop with fees/slippage/gap allowance and a liquidation buffer.
Modeled max loss ceiling is $0.50 (was $0.60), and that number is a HARD dollar stop on the live
position, not just a sizing input. Structural stop = signal candle low/high plus 0.05 ATR, at least
0.05% from entry, and never wider than the -10% net-margin ceiling.

On a restart or any monitoring gap >=45s, walk missed 1m candles: if the stop (or the hard dollar
stop) traded during a bar, fill at the stop; if the bar opened through it, fill at the gap open.
This is still paper and is not a guarantee against a true gap, but it stops an 8h silent hold from
marking to the resume price.

## Exits
Net TP +20% of the original $6 margin ($1.20); close 90%, keep 10% moonbag.
Target must lie within 4 ATR and net target / modeled risk >=1.5; otherwise skip.
Trail arms at +12% margin, follows peak by 5 percentage points.
Max hold 5 minutes; exception ONLY an already-realized 90% runner with an active trail.
Scratch: if the scalp is not at least +2% of margin after 2 minutes, flatten.
1-hour cooldown after any completed loss before the next entry.
No averaging down.

## Unchanged bankroll rules
$6 each, max 3 distinct symbols / $18 original-margin reserved; runner keeps its slot;
rolling 24h cap of 10 qualifying entries; two completed losses per Bogota day blocks entries;
equity <=$20 entry gate / below $20 flatten. Fresh v4 start is $25, so the $20 gate is still $5 of room.
Taker 0.045% each way, book-walk fills plus 0.02% each fill; costs are not guarantees.
Local JSONL alerts only. Supervisor is not reboot-persistent.

## Daily review log
- 14 Nov 2026 10:42 Bogota: 0 trades in 24h, equity $25. Kept v4 (hindsight: 17 late-rejected signals would have lost or gone flat).
- 15 Nov 2026 10:43 Bogota: 0 trades in 24h, 0W/0L, P&L $0.00, equity $25.00. No losses to target, so v4 kept unchanged (no version bump).
  Evidence: 4,614 skips in 24h, 91% on the 4x volume gate. Hindsight on the 13 breakouts that cleared 3-4x volume and >=0.75 ATR strength
  (HL 1m candles, next 5 min): 6/13 had more favorable than adverse excursion, avg 5-min result -0.076% price before ~0.13% round-trip costs.
  Loosening volume to 3x would have added losing trades, so it was rejected. Uptime gaps (largest 00:11-07:39 Bogota) were box/process pauses
  with the process still alive, not a rule issue. Restarted supervisor cleanly (one instance).
- 22 Nov 2026 10:45 Bogota (first review on GitHub Actions): 0 trades in 24h, 0W/0L, P&L $0.00, equity $25.00. No losses to target, so v4 kept unchanged (no version bump).
  Evidence: 974 scans, 34 raw breakout candidates, none passed the entry gates. Skip reasons are not in the repo (hl/data/skips.jsonl is gitignored), so no evidence supports loosening any gate.
  Uptime gaps between Actions runs: 19:15-01:02 and 06:47-07:56 Bogota (scheduled cron handoff delays). Latest run 45024878356 alive, heartbeat 10:39 Bogota.
- 23 Nov 2026 10:45 Bogota: 0 trades in 24h, 0W/0L, P&L $0.00, equity $25.00. No losses to target, so v4 kept unchanged (no version bump).
  Evidence: 865 scans, 26 raw breakout candidates, none passed the entry gates; skip reasons still not committed, so no evidence supports loosening any gate.
  Uptime gaps in committed scans: 13:41-15:07 and 20:51-05:00 Bogota (8h hole, likely Actions handoff/state sync). Latest run 45152089385 alive, heartbeat 10:39 Bogota.
- 24 Nov 2026 10:53 Bogota: 0 trades in 24h, 0W/0L, P&L $0.00, equity $25.00. No losses to target, so v4 kept unchanged (no version bump).
  Evidence: 691 scans, 29 raw breakout candidates, none passed the entry gates; skip reasons still not committed, so no evidence supports loosening any gate.
  Uptime: gap 23:04-01:37 Bogota, and no run at all from 07:22 to 10:45 Bogota (run 45341764824 ended, no hourly cron run queued behind it). Re-dispatched sims.yml as run 45429168762; fresh heartbeat 10:46 Bogota, all 3 sims alive.
- 25 Nov 2026 10:50 Bogota: 0 trades in 24h, 0W/0L, P&L $0.00, equity $25.00. No losses to target, so v4 kept unchanged (no version bump).
  Evidence: 922 scans, 26 raw breakout candidates, none passed the entry gates; skip reasons still not committed, so no evidence supports loosening any gate.
  Uptime gaps in committed scans: 16:30-22:16 (24 Nov) and 04:02-06:54 Bogota. Latest run 45608734590 alive, heartbeat 10:47 Bogota.
- 26 Nov 2026 10:52 Bogota: 0 trades in 24h, 0W/0L, P&L $0.00, equity $25.00. No losses to target, so v4 kept unchanged (no version bump).
  Evidence: 949 scans, 61 raw breakout candidates listed across scans, none passed the entry gates; skip reasons still not committed, so no evidence supports loosening any gate.
  Uptime gaps in committed scans: 12:39-15:53 and 21:38-22:19 (25 Nov), 04:04-08:23 Bogota. Latest run 45828652489 alive, heartbeat 10:46 Bogota.
- 27 Nov 2026 10:45 Bogota: 0 trades in 24h, 0W/0L, P&L $0.00, equity $25.00. No losses to target, so v4 kept unchanged (no version bump).
  Evidence: 971 scans, 26 raw breakout candidates listed across scans, none passed the entry gates; skip reasons still not committed, so no evidence supports loosening any gate.
  Uptime gaps in committed scans: 14:08-17:41 (26 Nov) and 05:25-09:31 Bogota. Latest run 46016697292 alive, heartbeat 10:38 Bogota.
- 28 Nov 2026 10:45 Bogota: 0 trades in 24h, 0W/0L, P&L $0.00, equity $25.00. No losses to target, so v4 kept unchanged (no version bump).
  Evidence: 1,018 scans, 26 raw breakout candidates listed across scans, none passed the entry gates; skip reasons still not committed, so no evidence supports loosening any gate. 2 error events logged.
  Uptime gaps in committed scans: 15:16-18:05 and 23:50-00:38 (27 Nov), 06:23-09:47 Bogota. Latest run 46156439681 alive, heartbeat 10:39 Bogota.
