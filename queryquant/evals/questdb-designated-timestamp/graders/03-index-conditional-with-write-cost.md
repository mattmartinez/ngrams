---
type: llm
focus: trace
weight: 1
---

report.md treats an index on device as conditional rather than a blanket yes: it explains that a symbol index narrows rows within partitions but does not prune partitions, states that writes to an indexed symbol column double (or an equivalent write-cost statement), ties that cost to the user's O3 lag concern, and recommends testing or an off-peak rollout before keeping it.
