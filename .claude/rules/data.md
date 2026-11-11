---
paths:
  - "src/ta_in/data/**"
  - "src/ta_in/news/**"
  - "src/ta_in/fundamentals/**"
---

# Data layer: point-in-time rule

- Every public data function takes an explicit `as_of` argument (timezone-aware, IST by default).
- Nothing returned may be dated or published after `as_of`: prices, fundamentals, news, filings, corporate actions.
- Filter by when the information became public, not only by the period it describes (e.g. results are usable from their announcement date, not quarter end).
- Never default `as_of` to "now" inside the data layer; the caller supplies it.
- Every data function needs an offline test proving that data after `as_of` is excluded.
