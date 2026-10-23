---
type: llm
focus: trace
weight: 1
---

report.md says QuestDB has no EXPLAIN ANALYZE and gives a concrete verification method: re-run EXPLAIN and check for an Interval scan (the Frame scan replaced by an Interval scan with an intervals: line), time the query warm from the client at least twice, and watch query_activity().
