---
type: llm
focus: trace
weight: 1
---

report.md addresses the aftermath of deleting 4.9M rows nightly: dead tuples must be reclaimed by (auto)vacuum, the default autovacuum thresholds are too coarse for a 410M-row table, and it proposes per-table autovacuum settings or an explicit VACUUM after the purge.
