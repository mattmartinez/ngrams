---
name: queryquant
description: Diagnose and fix a slow SQL query on PostgreSQL, MySQL/Aurora MySQL, or QuestDB — capture the plan safely, triage it with the bundled checker, rank statistics/index/rewrite fixes, verify, and roll out without locking the table. Use whenever the user shows a slow query, pastes EXPLAIN or a query plan, asks which index to add, mentions a seq scan, full table scan, filesort, nested loop, interval scan, missing or unused index, a SYMBOL index or SAMPLE BY query, a SELECT timing out, or one statement driving database CPU — even if they never say "optimize". Not for finding which statements are slow in logs or metrics, not for deadlocks or lock waits, and not for application-code profiling.
argument-hint: "[path/to/plan.json or paste the query + EXPLAIN output]"
---

# queryquant

Slow queries are almost always one dominant cause: a bad row estimate, a missing or wrong index, a sort or hash that spills, or a query shape the planner cannot use an index for. The job is to find that cause from the plan, prove it with numbers, and ship the cheapest fix that does not hurt writes or lock the table. Textbook rules ("avoid SELECT *") come last; evidence from the plan comes first.

## 1. Gather inputs

Ask for everything missing in one message. The user runs the commands and pastes output; track which environment each paste came from.

| Need | Why | How to get it |
|---|---|---|
| The statement with real parameter values | A generic/prepared plan can differ from the plan for the actual values | From the app, slow log, `pg_stat_statements`, or `performance_schema` |
| Engine and major version | Features and column names are version-gated; QuestDB speaks the PostgreSQL protocol but is not PostgreSQL | `SELECT version();` |
| Environment: prod, replica, staging | Decides what is safe to run (see §2) | Ask |
| Table DDL, existing indexes, row counts | Index proposals must account for what exists; a 2k-row table never needs an index | PG: `\d+ t`, `SELECT reltuples FROM pg_class WHERE oid='t'::regclass`. MySQL: `SHOW CREATE TABLE t`, `SHOW INDEX FROM t`, `SELECT table_rows FROM information_schema.tables WHERE table_name='t'` |
| What "slow" means | Latency target, calls per minute, p95 or one-off. A 2 s query that runs nightly is not a problem; a 50 ms query at 2k/s is | Ask, or read the statement stats |
| The plan | §2 | |

## 2. Capture the plan safely

`EXPLAIN ANALYZE` **executes the statement**. For an `INSERT`, `UPDATE`, or `DELETE` that means real writes unless rolled back. Default to the safest form the environment allows and say which one you used.

| Engine | Read-only prod (no write authorization) | SELECT, or DML where a rollback is acceptable |
|---|---|---|
| PostgreSQL | `EXPLAIN (FORMAT JSON) <stmt>` (estimates only) | `SET statement_timeout = '30s'; EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) <select>` — DML: `BEGIN; EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) <dml>; ROLLBACK;` |
| MySQL 8.0+ | `EXPLAIN FORMAT=JSON <stmt>` (estimates only) | `EXPLAIN ANALYZE <select>` (8.0.18+, TREE output) — DML: `START TRANSACTION; EXPLAIN ANALYZE <dml>; ROLLBACK;` |
| QuestDB | `EXPLAIN <stmt>` (never executes; no ANALYZE form exists) | Same; measure wall time from the client and watch `query_activity()` |

Prefer a replica or staging with production-like data for the ANALYZE run. ANALYZE on a `SELECT` still reads every row the query touches, so set a timeout. Save the output to a file; the checker reads it from a file or stdin.

## 3. Triage the plan

1. Run the checker. It parses Postgres JSON and plain-text plans (pasted `EXPLAIN ANALYZE` output works), MySQL JSON, MySQL `EXPLAIN ANALYZE` trees, and QuestDB `EXPLAIN` trees, and prints ranked findings: hot nodes by self time, row misestimates, disk sorts, hash batches, heavy nested loops, filtered seq scans, full scans, filesort, unused keys, and (QuestDB) full frame scans and non-JIT filters.

   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/explain_check.py plan.json          # ranked findings
   python3 ${CLAUDE_SKILL_DIR}/scripts/explain_check.py plan.json --verbose # every node and rule, to stderr
   python3 ${CLAUDE_SKILL_DIR}/scripts/explain_check.py plan.json --json    # machine-readable
   ```

2. Read the top finding first and trace its chain. The usual ones:
   - row misestimate → wrong join method → nested loop with 100k+ inner loops (fix statistics before adding indexes);
   - no usable index → seq/full scan with most rows removed by filter;
   - `ORDER BY` with no matching index → sort spills to disk or filesort;
   - MySQL `filtered` under 10% → the chosen index is not the selective one;
   - index-only scan with heap fetches → table needs vacuum, not a new index;
   - QuestDB `Frame forward scan` with no `Interval` scan → the time predicate is not on the designated timestamp.
3. Classify the cause: **statistics**, **index** (missing, wrong column order, not covering), **query shape** (non-sargable predicate, `OR`, `NOT IN`, `DISTINCT` hiding fan-out, `OFFSET`), **resources** (`work_mem`, `sort_buffer_size`), or **data design** (partitioning, materialization). Open the engine reference for the node glossary and catalog queries: `references/postgres.md`, `references/mysql.md`, or `references/questdb.md`.

## 4. Propose fixes, cheapest first

Order by cost to ship and risk, and say why a cheaper tier is not enough before moving to the next:

1. **Statistics** — `ANALYZE`, a higher statistics target on the skewed column, extended statistics for correlated columns. No schema change, no app deploy. Say what the corrected estimate would make the planner do; if the only alternative to the current plan is a scan of a huge table, the plan will not change and the hot node still needs tier 2 or 3.
2. **Index** — new, reordered, partial, expression, or covering. Costs: write amplification on every insert/update, disk, build time, and (PG) losing HOT updates on the indexed column. Check write rate on the table before proposing.
3. **Rewrite** — sargable predicates, `EXISTS` instead of `NOT IN`, keyset pagination, batching. Needs an app deploy; see `references/patterns.md`.
4. **Parameters** — session-scoped first (`SET LOCAL work_mem`), with the plan diff as evidence. Never a global change from one query.
5. **Schema** — partitioning, materialized views, denormalization. Last, because it changes every query on the table.

For each candidate state: the node it changes and the expected new plan shape, the cost, the risk, and how it will be verified. Do not propose planner toggles (`enable_seqscan`, `optimizer_switch`) for production, "filter in a subquery" (the planner already pushes predicates down), or `DISTINCT` to hide a join fan-out.

## 5. Verify

- Re-capture the plan with the same command and run the checker on both files. Compare execution time, buffers read, and the node that was supposed to change. If the hot node did not move, the diagnosis was wrong; go back to §3 rather than stacking fixes.
- Confirm the index is used: PG `Index Name` in the plan and `pg_stat_user_indexes.idx_scan` rising; MySQL `key` in the plan; QuestDB an `Interval` or `Index` scan replacing the `Frame` scan, plus warm wall time from the client (run twice).
- Try two or three other parameter values, including a skewed one, so the plan is not tuned to a single value.
- Check the write side after an index: insert/update latency and, for PG, `n_tup_hot_upd` on the table.

## 6. Roll out without locking

| Engine | Build | If it fails | After |
|---|---|---|---|
| PostgreSQL | `SET lock_timeout = '5s'; CREATE INDEX CONCURRENTLY idx ON t (...);` — not inside a transaction block | The index is left `INVALID`; `DROP INDEX CONCURRENTLY idx` and retry | `ANALYZE t;` then watch `pg_stat_statements` mean time for the statement |
| MySQL 8.0+ | `SET SESSION lock_wait_timeout = 5; ALTER TABLE t ADD INDEX idx (...), ALGORITHM=INPLACE, LOCK=NONE;` | The ALTER rolls back; retry off-peak | `ANALYZE TABLE t;` then watch the digest in `performance_schema` |
| QuestDB | `ALTER TABLE t ALTER COLUMN sym ADD INDEX;` (atomic, non-blocking) — repartitioning means recreating the table | | Re-time the query; check ingestion latency, since writes to an indexed symbol column double |

Large index builds on Aurora/RDS read replicas add replication lag; schedule them. `VACUUM FULL`, non-concurrent `REINDEX`, and `OPTIMIZE TABLE` lock the table and need a maintenance window.

## Report format

Use this structure so the user can paste it into a ticket:

```
## Query
<statement, engine + version, environment, calls/min, current latency>
## Diagnosis
<top checker findings with numbers; cause class; the chain>
## Fix (ranked)
1. <DDL or rewrite> — changes <node> from <X> to <Y>; cost <write/disk/build>; risk <...>
## Verification
<before/after: execution time, buffers, hot node; other parameter values tried>
## Rollout
<exact commands with lock_timeout / CONCURRENTLY / ALGORITHM=INPLACE; rollback command>
```

## Files

- `scripts/explain_check.py` — plan triage; run it, do not read it (`--help` for options)
- `references/postgres.md` — plan node glossary, catalog queries for slow/unused/missing indexes, lock-safe DDL
- `references/mysql.md` — EXPLAIN JSON and TREE glossary, `performance_schema` queries, online DDL
- `references/questdb.md` — EXPLAIN node glossary, designated timestamp and partition pruning, SYMBOL indexes, LATEST ON / SAMPLE BY / ASOF JOIN shapes, ingestion side effects
- `references/patterns.md` — index and rewrite patterns, each with when it applies and when it does not
