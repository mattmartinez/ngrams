---
type: llm
focus: trace
weight: 1
---

report.md gives a safe capture form: plain EXPLAIN (FORMAT JSON) on prod, and/or BEGIN; EXPLAIN (ANALYZE, BUFFERS) ...; ROLLBACK; with a statement_timeout on a snapshot restore or in a maintenance window (not on a hot-standby replica, where DML fails), or the equivalent SELECT count(*) with the same predicate on the replica for the scan side, and notes that a rolled-back DELETE still scans the table and writes WAL.
