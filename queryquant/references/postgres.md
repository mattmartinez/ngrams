# queryquant — PostgreSQL reference

Read this when the engine is PostgreSQL (or a compatible fork such as Aurora PostgreSQL). Facts below were checked against the current PostgreSQL docs in October 2026; versions are noted where they matter.

## Contents

1. Reading EXPLAIN (ANALYZE, BUFFERS)
2. Plan node glossary
3. Statistics and the planner
4. Index types
5. Catalog queries
6. Lock-safe DDL
7. Session knobs (diagnostic only)
8. Maintenance

## 1. Reading EXPLAIN (ANALYZE, BUFFERS)

```sql
EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) SELECT ...;   -- save the JSON, run explain_check.py
EXPLAIN (ANALYZE, BUFFERS) SELECT ...;                 -- text form for humans
```

- `Actual Total Time` and `Actual Rows` are **per-loop averages**. Multiply by `Actual Loops` to get the node's real work. A 0.05 ms index scan with `loops=400000` costs 20 s. PostgreSQL 18 prints fractional `Actual Rows`.
- A node's own time is its total minus its children's totals (both × loops). The checker does this and ranks nodes by it.
- `Plan Rows` vs `Actual Rows`: a ratio over 10× either way is a misestimate and the first thing to fix, because join method, join order, and index choice all follow from it.
- `Buffers: shared hit=… read=…`: `read` is I/O (or OS cache). `temp read/written` means a sort or hash spilled. PostgreSQL 18 includes BUFFERS automatically with ANALYZE.
- `Sort Method: external merge  Disk: 184320kB` means `work_mem` was too small for this sort.
- `Hash Batches: 16` (or `Original Hash Batches` differing) means the hash side did not fit in `work_mem`.
- `Rows Removed by Filter` on a scan: rows read then discarded. A large number next to a small `Actual Rows` is the signature of a missing index.
- `Heap Fetches` on an Index Only Scan: the visibility map was not set for those pages, so the heap was visited anyway. Fix with `VACUUM`, not with a different index.
- `Rows Removed by Index Recheck` on a Bitmap Heap Scan: the bitmap went lossy (`work_mem`) or the index cannot answer the predicate exactly.
- `Planning Time` large (tens of ms) on a short query: many partitions, many indexes, or stale cached plans.

## 2. Plan node glossary

| Node | Meaning | Worry when |
|---|---|---|
| Seq Scan | Reads the whole table | Table is large and most rows are removed by the filter. Correct for small tables or predicates matching most rows |
| Index Scan | Walks the index, fetches matching heap rows | High `loops` on the inner side of a nested loop; low selectivity (then Bitmap is better) |
| Index Only Scan | Answers from the index alone | `Heap Fetches` high |
| Bitmap Index Scan + Bitmap Heap Scan | Builds a bitmap of matching pages, then reads pages in order; used for mid-selectivity and for `OR` (BitmapOr) | Recheck removing many rows |
| Nested Loop | For each outer row, run the inner plan | Inner `loops` in the thousands with a Seq Scan or a costly Index Scan inside; outer row count misestimated |
| Hash Join | Hashes the smaller side, probes with the other | `Hash Batches > 1`; the hashed side is unexpectedly large (misestimate) |
| Merge Join | Both inputs sorted on the join key | Explicit Sorts feeding it that spill |
| Sort | Sorts the input | `external merge`; a matching index would remove it |
| Incremental Sort | Sort on a suffix when a prefix is already ordered | Rarely a problem |
| HashAggregate / GroupAggregate | Grouping by hash or on sorted input | HashAggregate spilling (`Disk Usage`) |
| Gather / Gather Merge | Parallel workers | `Workers Launched < Workers Planned` |
| Materialize | Buffers the inner side of a nested loop | Large inner side rescanned many times |
| CTE Scan | Reads a materialized CTE | CTEs are inlined since PG 12 unless `MATERIALIZED` or referenced more than once |
| SubPlan / InitPlan | Correlated subquery (SubPlan runs per row) | SubPlan with high loops: rewrite as a join |

## 3. Statistics and the planner

- `ANALYZE t;` refreshes row estimates. Autovacuum does it, but a table that just received a bulk load or that changes faster than `autovacuum_analyze_scale_factor` (default 10%) has stale stats. Check `pg_stat_user_tables.last_autoanalyze`.
- Skewed column: `ALTER TABLE t ALTER COLUMN c SET STATISTICS 1000;` then `ANALYZE t;` (default target 100).
- Correlated columns (city and zip, tenant and status): the planner multiplies selectivities as if independent and underestimates. `CREATE STATISTICS s (ndistinct, dependencies, mcv) ON a, b FROM t; ANALYZE t;` — `CREATE STATISTICS` since PG 10, `mcv` since PG 12, expressions since PG 14.
- Predicates the planner cannot estimate (functions of columns, casts, `LIKE` with parameters) get default selectivities. Rewrite them sargable (`patterns.md` §3) or add an expression index, which also gives the planner statistics on the expression.
- Prepared statements: the first five executions use custom plans; after that a generic plan is used if its estimated cost is not much higher than the average custom plan. A query that is fast in `psql` and slow from the app is often a generic plan on a skewed parameter. Check with `EXPLAIN EXECUTE`, and consider `SET plan_cache_mode = force_custom_plan` (PG 12+) for that connection or statement.

## 4. Index types

| Type | Use | Example |
|---|---|---|
| B-tree (default) | Equality, range, `ORDER BY`, `LIKE 'abc%'` (needs `C` collation or `text_pattern_ops`) | `CREATE INDEX CONCURRENTLY ON users (email)` |
| Hash | Equality only; rarely beats B-tree | |
| GIN | Arrays, `jsonb` containment, full text, trigram `LIKE '%abc%'` | `USING GIN (metadata jsonb_path_ops)`, `USING GIN (name gin_trgm_ops)` |
| GiST | Ranges, geometry, nearest-neighbour, exclusion constraints | `USING GIST (period)` |
| SP-GiST | Partitioned search trees (text prefixes, points) | |
| BRIN | Huge append-only tables where the column correlates with physical order (time series) | `USING BRIN (created_at)` |

`INCLUDE (cols)` adds payload columns to a B-tree for index-only scans without making them part of the key. Partial (`WHERE`) and expression indexes are in `patterns.md` §2.

## 5. Catalog queries

Statement stats (requires the `pg_stat_statements` extension; columns are `total_exec_time` / `mean_exec_time` from PG 13, `total_time` / `mean_time` on PG 12 and earlier):

```sql
SELECT calls, round(mean_exec_time::numeric, 2) AS mean_ms,
       round(total_exec_time::numeric) AS total_ms, rows, left(query, 120) AS query
FROM pg_stat_statements
ORDER BY total_exec_time DESC LIMIT 20;
```

Tables read by sequential scan (floor on size so small lookup tables do not dominate):

```sql
SELECT relname, seq_scan, seq_tup_read, idx_scan, n_live_tup,
       pg_size_pretty(pg_relation_size(relid)) AS size
FROM pg_stat_user_tables
WHERE seq_scan > 0 AND n_live_tup > 10000
ORDER BY seq_tup_read DESC LIMIT 20;
```

Unused indexes. Excludes indexes that back constraints (they must stay), and counts since the last stats reset only; on a primary, check the replicas too since reads there have their own counters:

```sql
SELECT s.schemaname, s.relname, s.indexrelname, s.idx_scan,
       pg_size_pretty(pg_relation_size(s.indexrelid)) AS size
FROM pg_stat_user_indexes s
JOIN pg_index i ON i.indexrelid = s.indexrelid
WHERE s.idx_scan = 0 AND NOT i.indisunique AND NOT i.indisprimary
  AND NOT EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conindid = s.indexrelid)
ORDER BY pg_relation_size(s.indexrelid) DESC;
SELECT stats_reset FROM pg_stat_database WHERE datname = current_database();
```

Duplicate indexes (same leading definition):

```sql
SELECT a.indrelid::regclass AS tbl, a.indexrelid::regclass AS idx_a, b.indexrelid::regclass AS idx_b
FROM pg_index a JOIN pg_index b
  ON a.indrelid = b.indrelid AND a.indexrelid < b.indexrelid
 AND a.indkey::text = b.indkey::text AND a.indclass::text = b.indclass::text;
```

Invalid indexes left by a failed `CREATE INDEX CONCURRENTLY`:

```sql
SELECT indexrelid::regclass FROM pg_index WHERE NOT indisvalid;
```

Vacuum lag (dead tuples block index-only scans and bloat the table):

```sql
SELECT relname, n_live_tup, n_dead_tup, n_tup_hot_upd, last_autovacuum, last_autoanalyze
FROM pg_stat_user_tables ORDER BY n_dead_tup DESC LIMIT 20;
```

Long transactions (they hold back vacuum and block DDL):

```sql
SELECT pid, now() - xact_start AS age, state, left(query, 80)
FROM pg_stat_activity WHERE xact_start IS NOT NULL ORDER BY age DESC LIMIT 10;
```

## 6. Lock-safe DDL

```sql
SET lock_timeout = '5s';                                   -- fail fast instead of queueing behind a long transaction
CREATE INDEX CONCURRENTLY idx_orders_user ON orders (user_id);   -- not inside BEGIN/COMMIT
```

- A failed concurrent build leaves the index marked `INVALID` (visible in `\d`). Either `DROP INDEX CONCURRENTLY` it and retry, or `REINDEX INDEX CONCURRENTLY` it (PG 12+).
- `DROP INDEX CONCURRENTLY` for removals; plain `DROP INDEX` takes an exclusive lock.
- `REINDEX ... CONCURRENTLY` (PG 12+) for bloated indexes; plain `REINDEX` locks writes.
- Constraints: `ALTER TABLE ... ADD CONSTRAINT ... NOT VALID;` then `VALIDATE CONSTRAINT` separately, so the long scan runs with a weaker lock.
- Run `ANALYZE t;` after creating an index so the planner has statistics on it, then watch the statement in `pg_stat_statements`.
- Indexing a column that is updated often disables HOT updates for that table's rows, which raises write amplification and bloat. Check `n_tup_hot_upd` before and after.

## 7. Session knobs (diagnostic only)

```sql
SET LOCAL work_mem = '256MB';          -- inside a transaction, for one query; prove the sort/hash stops spilling
SET LOCAL enable_nestloop = off;       -- the docs call this a "crude method" and a "temporary solution"
```

If disabling a planner method yields a much better plan, that is evidence of a statistics or cost problem (`random_page_cost` is 4 by default and too high for SSDs; `effective_cache_size` should reflect RAM). Fix the cause; do not ship the toggle.

## 8. Maintenance

- `VACUUM (ANALYZE) t;` reclaims dead rows for reuse and sets the visibility map. Safe online.
- `VACUUM FULL t;` rewrites the table and holds an exclusive lock for the duration. Maintenance window only; `pg_repack` is the online alternative.
- Lower `fillfactor` (e.g. 80) on tables with frequent updates of unindexed columns to keep HOT updates on-page.
- `REFRESH MATERIALIZED VIEW CONCURRENTLY v;` needs a unique index on the view that covers all rows and uses plain column names (no expression, no `WHERE`).
