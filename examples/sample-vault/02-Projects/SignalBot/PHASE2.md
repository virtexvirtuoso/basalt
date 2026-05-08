---
title: "SignalBot Phase 2 — Multi-signal stack"
created: 2026-03-14
updated: 2026-03-14
tags: [signalbot, phase2]
status: active
---

# SignalBot Phase 2 — Multi-signal stack

> Building the combined OFI + VPIN + cascade-prediction stack proposed in [[HYPOTHESIS]].

## Why now

Phase 1 (latency-only) plateaued at the level we predicted in [[HYPOTHESIS]] —
sub-1% per cycle, no edge growth. Confirmed the structural-signal direction.

## Architecture

Three signals fused via Bayesian combination, gated by a regime classifier.
OFI is fast (10ms ticks); VPIN is slow (1m buckets); cascade-prediction fires
on liquidation-cluster detection.

## Validation plan

Walk-forward across 4 venues. Targeting 50% out-of-sample direction accuracy
on 5-min bars, conditional on signal-fusion confidence.

## See Also
- [[HYPOTHESIS]]
- [[BACKTEST]]
- [[Microstructure-Primer]]
