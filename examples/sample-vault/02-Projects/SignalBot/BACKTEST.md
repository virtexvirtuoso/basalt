---
title: "SignalBot Backtest — Q1 2026"
created: 2026-03-21
updated: 2026-04-05
tags: [signalbot, backtest, validation]
status: active
confidence: HIGH
---

# SignalBot Backtest — Q1 2026

> First out-of-sample test of the combined stack from [[PHASE2]]. Confirms the
> structural-signal thesis from [[HYPOTHESIS]].

## Setup

- **Period:** 2024-01 → 2025-12 (in-sample), 2026-01 → 2026-03 (out-of-sample)
- **Universe:** BTC, ETH, SOL perps on three venues
- **Signal:** OFI(10ms) + VPIN(1m) + cascade-prediction, Bayesian fusion

## Results

| Metric | IS | OOS |
|--------|-----|-----|
| Direction accuracy (5m) | 56.2% | **53.8%** |
| Sharpe | 1.94 | **1.41** |
| Max drawdown | -8.1% | -11.3% |

## Verdict

OOS Sharpe 1.41 with the multi-signal stack vs ~0.6 with latency-only. The
edge is real, structural, and consistent with [[HYPOTHESIS]].

## See Also
- [[HYPOTHESIS]]
- [[PHASE2]]
- [[CALIBRATION]]
