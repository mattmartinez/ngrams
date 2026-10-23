---
tags: [skill-creator]
max_turns: 25
timeout_seconds: 600
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit, Bash]
---

this dashboard query on postgres is killing us, p95 is over 3 seconds. I already grabbed EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) on our staging copy (a 1:9 sample of prod) into plan.json and the DDL + stats into schema.sql. tell me what's actually wrong and what to change, and write it up as report.md so I can paste it in the ticket. we can't take locks on prod during the day.

Input files: plan.json, schema.sql
