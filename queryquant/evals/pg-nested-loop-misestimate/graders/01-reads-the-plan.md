---
type: llm
focus: trace
weight: 1
---

report.md states that the users scan was estimated at about 2 rows but returned 587 (a roughly 300x underestimate), that this propagates to the join estimate (about 40 estimated vs 13,472 actual), and that the inner scan on orders running 587 times is the largest single cost in the plan.
