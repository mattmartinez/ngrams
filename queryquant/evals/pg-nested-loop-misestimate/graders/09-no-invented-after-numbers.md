---
type: llm
focus: trace
weight: 1
---

report.md does not state measured after-fix latencies or row counts as facts; any post-fix numbers are labelled as expectations or estimates; the before numbers quoted match plan.json (about 25 ms execution, 2 vs 587 rows, 587 loops, 13,472 joined rows), and the report does not mix staging numbers with prod's 3.1 s as if they were the same measurement.
