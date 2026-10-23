---
type: llm
focus: trace
weight: 1
---

report.md sets lock_timeout (or an equivalent fail-fast lock setting) for the index rollout and gives an explicit DROP INDEX CONCURRENTLY (or REINDEX CONCURRENTLY) command for an INVALID index left by a failed build.
