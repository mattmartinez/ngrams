---
type: llm
focus: trace
weight: 1
---

report.md identifies that bounding ts can drop late-arriving rows (rows whose ts is older than the bound but whose ingested_at is inside the window), and proposes a concrete way to size the margin or check equivalence (for example measuring max(ingested_at - ts) over a recent partition, or comparing row counts of old and new queries), rather than picking an arbitrary margin.
