---
type: llm
focus: trace
weight: 1
---

report.md's first-ranked fix is to bound the designated timestamp ts in the WHERE clause (with an explicit margin for ingestion delay) so the plan becomes an Interval scan with partition pruning, ranked ahead of adding a symbol index.
