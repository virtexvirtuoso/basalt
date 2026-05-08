---
title: "SignalBot Calibration System"
created: 2026-04-02
updated: 2026-04-15
tags: [signalbot, calibration, infrastructure]
status: active
---

# SignalBot Calibration System

> Online recalibration loop for the multi-signal stack. Re-fits Bayesian weights
> daily based on rolling 30-day performance.

## Why

[[BACKTEST]] showed solid OOS performance, but signal weights drift across
regimes. The structural-edge claim from [[HYPOTHESIS]] is robust to drift; the
specific *combination* weights are not.

## Approach

- Nightly job pulls last 30d of fills
- Re-fits the Bayesian fusion via grid search
- New weights deployed at 00:30 UTC after sanity checks
- Old weights kept for 7 days for rollback

## Open issues

The cascade-prediction signal has irregular firing — may need its own
half-life parameter rather than the unified one we use for OFI/VPIN.

## See Also
- [[HYPOTHESIS]]
- [[BACKTEST]]
- [[PRODUCTION-NOTES]]
