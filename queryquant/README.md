# queryquant

Diagnose and fix a slow SQL query on PostgreSQL, MySQL (including Aurora MySQL), or QuestDB. The skill reads the query plan, finds the one node that costs the most, ranks the fixes from cheapest to most expensive, and writes the rollout so nothing locks the table.

## Usage

```
/queryquant <the query, the plan, or a path to a saved plan>
```

You can also just describe the problem. The skill triggers on phrases like "this query is slow", "which index should I add", a pasted `EXPLAIN`, a seq scan, a filesort, or a QuestDB `SAMPLE BY` that takes too long.

It works best with four things. It asks for any that are missing:

- the statement, with the real parameter values
- the engine and version (`SELECT version();`)
- the table DDL, existing indexes, and row counts
- the plan, saved to a file

## Examples

```
/queryquant the account dashboard query takes 3 s, plan is in plan.json and the DDL is in schema.sql
```

```
/queryquant Aurora MySQL, open-orders query is slow for small merchants. Which index, and is it safe to add during the day?
```

```
/queryquant our nightly DELETE on prod takes 40 minutes and causes lock waits
```

```
/queryquant questdb: recent readings for one device take 40 s. Should I index the device column?
```

## What you get

A report you can paste into a ticket:

1. **Query**: the statement, engine, environment, call rate, and current latency.
2. **Diagnosis**: the checker's top findings with numbers, the cause, and the chain from cause to slowness.
3. **Fix (ranked)**: statistics first, then indexes, then query rewrites, then settings, then schema changes. Each fix says which plan node it changes, what it costs on the write side, and the risk.
4. **Verification**: how to prove the fix worked, using the same `EXPLAIN` and at least one other parameter value.
5. **Rollout**: exact commands with lock timeouts and online DDL, plus the rollback command.

## Safety defaults

The skill assumes production is read-only unless you say otherwise.

- `EXPLAIN ANALYZE` runs the statement. For an `INSERT`, `UPDATE`, or `DELETE` the skill gives you plain `EXPLAIN`, or wraps the statement in `BEGIN` … `ROLLBACK` for a replica or staging copy.
- Every PostgreSQL index uses `CREATE INDEX CONCURRENTLY` with `lock_timeout`, plus the cleanup command if the build fails.
- Every MySQL index uses `ALGORITHM=INPLACE, LOCK=NONE` with `lock_wait_timeout`.
- It does not recommend planner toggles such as `enable_nestloop = off` for production.

## The plan checker

`scripts/explain_check.py` reads a saved plan and prints ranked findings. It never connects to a database and needs only Python 3.

```bash
python3 scripts/explain_check.py plan.json            # ranked findings
python3 scripts/explain_check.py plan.json --verbose  # also logs every node and rule to stderr
python3 scripts/explain_check.py plan.json --json     # machine-readable
psql -XAtc "EXPLAIN (FORMAT JSON) SELECT ..." | python3 scripts/explain_check.py -
```

It detects the format on its own:

| Engine | Formats |
|---|---|
| PostgreSQL | `EXPLAIN (FORMAT JSON)`, plain-text `EXPLAIN`, with or without `ANALYZE` and `BUFFERS`, with or without the psql table wrapper |
| MySQL | `EXPLAIN FORMAT=JSON`, `EXPLAIN ANALYZE` tree output |
| QuestDB | `EXPLAIN` text, with or without the psql wrapper |

Example output on a MySQL plan:

```
plan: mysql-json (estimates only)  nodes: 3  query_cost: 31,663.0
MEDIUM my.index-scan-filter     table orders (index via idx_orders_created) [path query_block.ordering_operation.table]
       ordered index walk on idx_orders_created filtering 0.50% (397 rows/scan is a LIMIT-scaled estimate); worst case reads the whole index
       hint: The optimizer walks the ORDER BY index and filters; fast only when matches are dense near the start. A composite index with the equality columns first and the ORDER BY column last lets the LIMIT stop after N entries for every value.
LOW    my.no-analyze            (whole plan)
       Estimates only. Capture EXPLAIN ANALYZE (FORMAT=TREE) when it is safe to execute: it runs the statement.
       hint: Without actual rows and times, hot-node and misestimate rules cannot run.
```

The full rule list is in the script's docstring.

## Files

| Path | Contents |
|---|---|
| `SKILL.md` | The procedure the skill follows |
| `references/postgres.md` | Plan node glossary, catalog queries, lock-safe DDL |
| `references/mysql.md` | `EXPLAIN` JSON and tree glossary, `performance_schema` queries, online DDL |
| `references/questdb.md` | Plan nodes, designated timestamp and partitions, `SYMBOL` indexes, ingestion side effects |
| `references/patterns.md` | Index and rewrite patterns, each with when it does and does not apply |
| `scripts/explain_check.py` | The plan checker |
| `scripts/fixtures/` | Plans captured from PostgreSQL 16, MySQL 8.0, and QuestDB 10.1 |
| `evals/` | Test cases for the skill itself; not installed |

## Tests

```bash
python3 -I scripts/test_explain_check.py
```
