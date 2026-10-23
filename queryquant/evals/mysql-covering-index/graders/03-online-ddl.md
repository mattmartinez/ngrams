---
type: llm
focus: trace
weight: 1
---

The DDL in report.md satisfies all four of: (a) ALTER TABLE ... ADD INDEX with ALGORITHM=INPLACE and LOCK=NONE, or an explicit statement that the build is online with concurrent DML; (b) a lock_wait_timeout or equivalent fail-fast setting; (c) ANALYZE TABLE after the build; (d) a mention of Aurora replica lag or of scheduling the build off-peak.
