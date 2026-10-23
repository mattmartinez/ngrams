# queryquant — deep dive on one query

Read this when the user asks to look at one finding from the scan ("look at 3"), pastes a plan, or asks why one statement is slow. It ends with the ticket card. Script paths below are relative to the skill's base directory (the folder holding SKILL.md); run them from there or prefix that path.

**When the finding is an N+1, an unbounded read, or an unchunked batch, skip the plan steps.** The problem
is the number of queries or rows, not one slow plan, and every lookup is often already indexed. Read the
statements involved and who calls them, write the bulk replacement (one query per lookup kind, matched to
rows in code, with `@BindList` IN-lists chunked when the id list is unbounded), and use the call-count
check below instead of an `EXPLAIN`:

```sql
-- MySQL, on a staging reader, before and after one call of the endpoint
SELECT LEFT(DIGEST_TEXT, 90) AS query, COUNT_STAR AS calls, ROUND(SUM_TIMER_WAIT/1e12, 2) AS total_s
FROM performance_schema.events_statements_summary_by_digest
WHERE SCHEMA_NAME = '<schema>' AND DIGEST_TEXT LIKE '%<table>%'
ORDER BY total_s DESC LIMIT 10;
```

The per-row statements should stop growing by N per call; each bulk statement grows by one.

## 1. Gather inputs

The skill never runs SQL itself. Hand the user each command, say where to run it (a staging reader by default), and wait for the pasted output.

Ask for everything missing in one message, and track which environment each paste came from. When the deep dive starts from a scan finding, most of this is already known: the statement and its file come from the scan, and the table's indexes from the Liquibase changesets. Ask only for what is missing, usually the engine version, the environment, row counts, and the plan.

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

Prefer a replica or staging with production-like data for the ANALYZE run. ANALYZE on a `SELECT` still reads every row the query touches, so set a timeout. Ask the user to paste the output, save it to a file, and run the checker on it. Bind values: ask for a realistic one when the choice matters, such as a tenant or account id that is unusually large; otherwise substitute a literal and say it is a placeholder.

## 3. Triage the plan

1. Run the checker. It parses Postgres JSON and plain-text plans (pasted `EXPLAIN ANALYZE` output works), pasted `pg_stat_statements` output, MySQL JSON, MySQL `EXPLAIN ANALYZE` trees, and QuestDB `EXPLAIN` trees, and prints ranked findings: hot nodes by self time, row misestimates, disk sorts, hash batches, heavy nested loops, filtered seq scans, full scans, filesort, unused keys, and (QuestDB) full frame scans and non-JIT filters.

   ```bash
   python3 scripts/explain_check.py plan.json          # ranked findings
   python3 scripts/explain_check.py plan.json --verbose # every node and rule, to stderr
   python3 scripts/explain_check.py plan.json --json    # machine-readable
   ```

2. Read the top finding first and trace its chain. The usual ones:
   - row misestimate → wrong join method → nested loop with 100k+ inner loops (fix statistics before adding indexes);
   - no usable index → seq/full scan with most rows removed by filter;
   - `ORDER BY` with no matching index → sort spills to disk or filesort;
   - MySQL `filtered` under 10% → the chosen index is not the selective one;
   - index-only scan with heap fetches → table needs vacuum, not a new index;
   - QuestDB `Frame forward scan` with no `Interval` scan → the time predicate is not on the designated timestamp;
   - `pg.generic-plan` → a prepared statement switched to a generic plan; fast in psql, slow from the app. Compare with a custom plan before touching statistics;
   - `pg.no-partition-pruning` → a function on the partition key stops pruning; rewrite the predicate, do not add indexes;
   - `pg.n-plus-one` → the plan is fine and the call count is the problem; batch the lookups, no index.
3. Classify the cause: **statistics**, **index** (missing, wrong column order, not covering), **query shape** (non-sargable predicate, `OR`, `NOT IN`, `DISTINCT` hiding fan-out, `OFFSET`), **resources** (`work_mem`, `sort_buffer_size`), or **data design** (partitioning, materialization). Open the engine reference for the node glossary and catalog queries: `references/postgres.md`, `references/mysql.md`, or `references/questdb.md`.

## 4. Propose fixes, cheapest first

Order by cost to ship and risk, and say why a cheaper tier is not enough before moving to the next:

1. **Statistics** — `ANALYZE`, a higher statistics target on the skewed column, extended statistics for correlated columns. No schema change, no app deploy. Say what the corrected estimate would make the planner do; if the only alternative to the current plan is a scan of a huge table, the plan will not change and the hot node still needs tier 2 or 3.
2. **Index** — new, reordered, partial, expression, or covering. Costs: write amplification on every insert/update, disk, build time, and (PG) losing HOT updates on the indexed column. Check write rate on the table before proposing.
3. **Rewrite** — sargable predicates, `EXISTS` instead of `NOT IN`, keyset pagination, batching. Needs an app deploy; see `references/patterns.md`.
4. **Parameters** — session-scoped first (`SET LOCAL work_mem`), with the plan diff as evidence. Never a global change from one query.
5. **Schema** — partitioning, materialized views, denormalization. Last, because it changes every query on the table.

For each candidate, work out the node it changes and the expected new plan shape, the cost, the risk, and how to verify it. The report shows only the winner; the others get one line each in the evidence block. Do not propose planner toggles (`enable_seqscan`, `optimizer_switch`) for production, "filter in a subquery" (the planner already pushes predicates down), or `DISTINCT` to hide a join fan-out.

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

The reader is a busy engineer pasting this into a ticket. Write the ticket card below and nothing else: plain words, short sentences, the answer before the reasoning. Keep the five sections under about 250 words, not counting SQL blocks and the evidence block.

- Say what the database is doing in everyday terms ("reads the whole table newest-first and checks each row"), not in plan field names. If a field name or number from the plan is needed, say what it means in the same sentence.
- Give one recommended fix. Other options go in the evidence block, one line each with why they lost.
- Put every number from the plan, the checker's findings, and the reasoning in the collapsed evidence block. The five sections hold only what the reader acts on.
- When the plan came from staging or a sample and the user's numbers are from prod, say which is which.
- In chat, write the evidence as a plain `### Evidence` section, because the terminal does not render `<details>`. Use the collapsed `<details>` block only when the user asks for ticket or PR text.
- If the user asked for something unsafe, such as `EXPLAIN ANALYZE` of a `DELETE` on prod, say so in the first sentence of What's wrong and give the safe command in Check it worked.

```
## What's wrong
<two or three sentences: what the database does today and why that is slow>

## Fix
<one sentence: what to change and whether it is safe during the day>
<one block, ready to run. DDL carries a lock timeout and CONCURRENTLY or ALGORITHM=INPLACE, LOCK=NONE, then ANALYZE. A query rewrite shows the new query.>

## Cost
<one or two sentences: write overhead in the user's own rates, disk, build time, and any side effect such as dead rows to vacuum or replica lag>

## Check it worked
<one or two sentences: re-run the same capture and compare execution time and rows or buffers read; name the node that should change>

## Undo
<the rollback: one SQL block for DDL, or "revert the deploy" for a query change>

<details><summary>Evidence</summary>

- Checker findings, with their rule names and numbers
- The chain from cause to slowness, naming the node that costs the most and its share of the runtime
- Safety notes the reader could get wrong, such as what a rolled-back statement still does
- Other options considered, one line each, and why they were not chosen
- Anything not verified
</details>
```
