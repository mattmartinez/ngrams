---
type: llm
focus: trace
weight: 1
---

report.md states that the plan does a full Frame scan (forward or backward) over every partition because the time predicate is on ingested_at rather than the designated timestamp ts, and that this is the main cause of the 40-second runtime.
