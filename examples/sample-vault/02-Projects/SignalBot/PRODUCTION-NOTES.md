---
title: "SignalBot Production Notes"
created: 2026-04-18
updated: 2026-05-02
tags: [signalbot, production, ops]
status: active
---

# SignalBot Production Notes

> Live since 2026-04-12. Tracking what's been hit, what's been dodged, what
> the [[HYPOTHESIS]] said vs what's actually happening.

## Live performance (3 weeks)

Tracking the [[BACKTEST]] OOS numbers in production:
- Direction accuracy: 54.1% (close to the 53.8% OOS prediction)
- Sharpe: 1.38 (vs 1.41 OOS)
- Max DD: -7.4% so far

## Surprises

The cascade-prediction signal, which [[HYPOTHESIS]] called "unique to crypto perps and not commoditized," is in fact more concentrated than backtests showed — three days of the live period contributed 60% of cascade-driven P&L. Concerning concentration.

## Calibration cadence

[[CALIBRATION]] is running as designed; weights drift modestly day-to-day.
[[Microstructure-Primer]] half-life question remains open.

## See Also
- [[HYPOTHESIS]]
- [[BACKTEST]]
- [[CALIBRATION]]
