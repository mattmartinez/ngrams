---
tags: [skill-creator]
max_turns: 25
timeout_seconds: 600
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit, Bash]
---

Aurora MySQL. The open-orders query is slow for some merchants (p95 1.4s, mostly accounts with few or no open orders). I pulled EXPLAIN FORMAT=JSON on our staging copy for customer 88213 (the account we had handy) into plan.json and the table DDL plus some stats into schema.sql. Which index should we add (exact DDL please), is it safe to add during the day, and what does it cost us on the write side? Put the answer in report.md.

Input files: plan.json, schema.sql
