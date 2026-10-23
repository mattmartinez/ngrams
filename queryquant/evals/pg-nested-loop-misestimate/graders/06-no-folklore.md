---
type: llm
focus: trace
weight: 1
---

report.md does not recommend removing SELECT columns, moving the filter into a subquery, enabling or disabling planner methods (enable_nestloop, enable_sort) in production, or a global work_mem change.
