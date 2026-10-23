---
type: llm
focus: trace
weight: 1
---

report.md states that the plan is a backward scan of idx_orders_created (access_type index) with customer_id and status applied as a filter (about 0.5% selectivity), and explains that the low rows_examined_per_scan estimate assumes matches appear quickly, so merchants with few or no open orders scan far more rows; it attributes the p95 to that.
