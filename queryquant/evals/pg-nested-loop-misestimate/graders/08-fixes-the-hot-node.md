---
type: llm
focus: trace
weight: 1
---

report.md addresses the hot node directly: it proposes a covering index on orders (user_id, created_at) INCLUDE (id, total) or an equivalent index-only path for the 587 probes, and/or a query restructuring (time-bounded created_at range or denormalisation) that stops materialising all 13,472 joined rows to return 100, and states the trade-off of each.
