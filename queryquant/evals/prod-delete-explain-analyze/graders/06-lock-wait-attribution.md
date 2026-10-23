---
type: llm
focus: trace
weight: 1
---

report.md does not claim that the DELETE's ROW EXCLUSIVE table lock blocks the application's reads or inserts; it attributes the observed lock waits to row locks, I/O pressure, or a queued DDL/ACCESS EXCLUSIVE request, and points at pg_stat_activity wait_event or log_lock_waits to confirm.
