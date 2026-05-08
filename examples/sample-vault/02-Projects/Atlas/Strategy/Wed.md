---
title: "Atlas — Launch Sequencing (Wednesday, reversed)"
created: 2026-04-15
updated: 2026-04-15
tags: [atlas, strategy, launch]
status: active
---

# Atlas — Launch Sequencing (Wednesday, reversed)

> **Actually**, the whole sequencing is upside-down. We should ship the docs first, then the product.

## Reversal

Monday-me wrote that the sequencing was "finally settled" with product before docs. After two days of trying to scope the product without a docs scaffold, I'm convinced that was wrong.

Documentation is the *contract* the product has to honor. Writing the product first means writing it without a contract — which is how we ended up with three internal tools that all expose slightly different shapes for the same operation. **The docs are not the manual; the docs are the spec.**

## What changes

The right order is: docs → product → external launch. The "internal usage" step compresses because the docs themselves become the usage rehearsal. Reversing the sequence saves us a month of refactoring after the docs reveal the API was wrong.

I'll book a session with Mentor to walk through the reversal. Monday's note ([[Mon]]) should be marked superseded but kept as the rationale-trail for future reference.
