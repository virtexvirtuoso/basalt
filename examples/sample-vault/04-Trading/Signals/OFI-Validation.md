---
title: "OFI Walk-Forward Validation"
created: 2026-04-08
updated: 2026-04-08
tags: [signals, ofi, validation]
status: validated
confidence: HIGH
---

# OFI Walk-Forward Validation

> Independent validation of order-flow imbalance as a directional signal.
> Confirms a piece of the structural-edge thesis: OFI survives multiple-testing
> correction across 4 of 6 venues.

## Methodology

- 18 months of BTC/ETH/SOL perp tick data
- 10ms OFI computed per venue
- Bonferroni correction across 132 venue×asset×horizon combinations

## Result

70 / 132 combinations pass at α=0.05 post-correction. Concentrated in 4 venues
(Binance, Bybit, OKX, Hyperliquid). Two venues (KuCoin, Gate) show no
significant signal — likely a liquidity-fragmentation artifact.

## Implication

Confirms that microstructure signals exist as predicted, and that they're
venue-specific. Not a price signal — an order-flow signal. Rules out a
generic "flow follows flow" interpretation.
