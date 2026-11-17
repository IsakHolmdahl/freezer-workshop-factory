---
name: code-review-and-quality
description: >-
  Alias for `code-review`: use the canonical five-axis checklist and severity
  vocabulary (`Critical`, `Required`, `Nit`, `Optional`, `FYI`) from `code-review`.
---

# Code Review and Quality

Defer to `code-review` for all review axes, severity labels (`Critical`, `Required`,
`Nit`, `Optional`, `FYI`), and report formatting. When self-checking your own change
before handoff:

1. Keep diffs focused and small (~100–300 lines where practical) and separate refactoring
   from behavior changes.
2. Verify all five axes (Correctness, Readability, Architecture, Security, Performance)
   and ensure every new behavior or bug fix has a regression test.
3. Run the repo's build, lint, and test gates before closing your bead.
