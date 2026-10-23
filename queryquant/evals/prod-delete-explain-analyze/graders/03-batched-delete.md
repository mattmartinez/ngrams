---
type: llm
focus: trace
weight: 1
---

report.md recommends deleting in bounded batches (by primary key or time range, a few thousand to tens of thousands of rows per short transaction) with lock_timeout, instead of one 4.9M-row transaction, and explains the lock/WAL/replica-lag reason.
