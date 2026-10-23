---
type: llm
focus: trace
weight: 1
---

report.md proposes a composite index on orders whose columns are customer_id, then status, then created_at (in that order), and explains the order (equality columns first, then the ORDER BY column so the LIMIT can stop early).
