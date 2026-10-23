---
type: llm
focus: trace
weight: 1
---

report.md proposes an index on users covering region and signup_source (composite or partial) and justifies it by the users scan's filter removing about 40,576 rows (the signup_source predicate is applied after the region index), independent of the join fix.
