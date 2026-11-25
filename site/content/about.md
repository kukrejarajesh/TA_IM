---
title: "About"
tags: ["about"]
---

**Paper trading only. This is not investment advice.** These reports were written by AI agents from the data available at the time shown.

## What this is

TradingAgents-IN is a Python rebuild of TradingAgents v0.7.0 for NSE stocks. A team of AI
agents works in this order:

1. Three analysts (market, news, fundamentals) write reports in parallel.
2. A bull and a bear researcher debate; a research manager writes the plan.
3. A trader turns the plan into a proposal.
4. Three risk analysts (aggressive, neutral, conservative) debate the proposal.
5. A portfolio manager gives the final rating on a five-tier scale
   (Buy, Overweight, Hold, Underweight, Sell).

A claim checker lists numbers in the proposal and decision that match no source.

## Honest limits

- Fundamentals are available for current dates only; for past dates they are withheld.
- News reaches back about 30 days.
- A report can be a **gap** (the analyst failed its own checks) or flagged
  **insufficient data**; such pages are published as they are, with a tag.
- Numbers the model derived itself (for example a percentage it computed) can appear in the
  claim-check list as unmatched; that does not always mean they are wrong.
- Rows marked replay are free re-runs of a recorded run, not new analyses.
- Nothing here places, modifies or cancels a real order.
