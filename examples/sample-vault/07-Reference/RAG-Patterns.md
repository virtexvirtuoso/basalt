---
title: "RAG Patterns — Reading Notes"
created: 2026-03-08
updated: 2026-03-08
tags: [reference, ai]
status: draft
---

# RAG Patterns — Reading Notes

> Unrelated to SignalBot — collecting notes on retrieval-augmented generation
> for a side interest. Not currently load-bearing for any project.

## Reranking

Cross-encoder reranking after ANN retrieval consistently helps when the
embedding model is small. ColBERT-style late-interaction is harder to deploy.

## Hybrid retrieval

BM25 + vector hybrid wins in most public benchmarks. The weight blend
matters less than people think; equal-weight is usually within 5% of tuned.

## Open thoughts

Could RAG patterns inform how I think about regime conditioning in trading?
The "retrieve relevant context, condition the model" pattern is structurally
similar.
