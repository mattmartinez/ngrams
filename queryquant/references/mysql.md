# queryquant — MySQL / InnoDB reference

Read this when the engine is MySQL 8.0+, Aurora MySQL, or MariaDB (MariaDB differs in places; check its docs for EXPLAIN ANALYZE and online DDL). Facts below were checked against the MySQL 8.0, 8.4 and 9.x docs in October 2026.

## Contents

1. Capturing a plan
2. Reading the plan
3. InnoDB specifics that shape every plan
4. Statistics and histograms
5. Catalog queries
6. Online DDL
7. Hints and session knobs (diagnostic only)

## 1. Capturing a plan

| Command | What you get | Executes? |
|---|---|---|
| `EXPLAIN FORMAT=JSON <stmt>` | Estimates: access type, keys, `rows_examined_per_scan`, `filtered`, cost. The checker parses this | No |
| `EXPLAIN FORMAT=TREE <stmt>` | Iterator tree with estimated cost and rows | No |
| `EXPLAIN ANALYZE <stmt>` (8.0.18+) | The TREE with `(actual time=first..last rows=N loops=L)` per iterator. The checker parses this | **Yes**, full execution. Wrap DML in `START TRANSACTION; ...; ROLLBACK;` |
| `EXPLAIN ANALYZE FORMAT=JSON` | Errors on 8.0 and 8.4. On 9.x only with `explain_json_format_version = 2` | Yes |

`actual time` and `rows` in the TREE are per-loop averages; multiply by `loops`. A `Single-row index lookup` at 0.01 ms with `loops=250000` is 2.5 s.

## 2. Reading the plan

### access_type (JSON `access_type`, traditional `type`), best to worst

| Value | Meaning | Worry when |
|---|---|---|
| `system`, `const` | One row via PK or unique index | never |
| `eq_ref` | One row per outer row via PK/unique | high `loops` is still fine; this is the ideal inner side |
| `ref` | Index lookup, several rows per key | `rows_examined_per_scan` large or `filtered` low |
| `fulltext` | FULLTEXT index | |
| `ref_or_null` | `ref` plus a NULL probe | |
| `index_merge` | Two or more indexes combined (`OR`) | often better as a composite index or `UNION ALL` |
| `unique_subquery`, `index_subquery` | `IN (subquery)` via index | |
| `range` | Index range (`BETWEEN`, `>`, `IN (list)`) | range covers most of the table |
| `index` | Full **index** scan (reads every index entry) | large table; usually an `ORDER BY`/`GROUP BY` satisfied by the index with no selective prefix |
| `ALL` | Full table scan | large table; no usable index for the join or filter column |

### Fields and flags

- `possible_keys` non-empty but `key: null`: the optimizer rejected the index. Causes: type or collation mismatch between the compared columns (implicit conversion), `filtered` so high that a scan is cheaper, or stale statistics.
- `filtered`: estimated percent of examined rows that survive `attached_condition`. Under 10% with thousands of `rows_examined_per_scan` means the chosen index is not the selective one; a composite index that includes the filter columns fixes it.
- `rows_examined_per_scan` × (rows of the outer tables) ≈ rows read. `rows_produced_per_join` is what reaches the next table.
- `using_index: true` (`Extra: Using index`): covering index, no clustered-index lookup per row.
- `using_index_condition` (`Using index condition`): Index Condition Pushdown; the filter runs inside the storage engine before fetching rows.
- `using_filesort`: a sort the index could not provide. Cheap for a few hundred rows; a problem on tens of thousands, and it may spill to disk (`sort_buffer_size`).
- `using_temporary_table`: `GROUP BY`/`DISTINCT`/`UNION` built a temp table; large ones go to disk (`tmp_table_size`, `internal_tmp_mem_storage_engine`).
- `using_join_buffer` (`Using join buffer (hash join)` / `(Block Nested Loop)`): the joined table has no usable index on the join column, so MySQL buffers and hashes instead. Add the index.
- TREE node vocabulary: `Table scan on t` (ALL), `Index scan on t using idx` (index), `Index range scan`, `Index lookup on t using idx (col=...)` (ref), `Single-row index lookup` (eq_ref/const), `Covering index lookup`, `Filter: (...)`, `Sort: col`, `Temporary table`, `Aggregate`, `Nested loop inner join`, `Hash join`, `Limit: N row(s)`.

## 3. InnoDB specifics that shape every plan

- The table **is** the primary key (clustered index). Rows are stored in PK order; a range scan on the PK is sequential I/O. A random or wide PK (UUIDv4, long strings) scatters inserts and bloats every secondary index.
- Every secondary index entry stores the PK columns. A secondary-index lookup for columns not in the index costs an extra PK lookup per row. A **covering** index (filter + selected columns) removes that lookup and shows `Using index`.
- Composite index order follows the same rule as everywhere: equality columns first, then one range, then `ORDER BY` columns (`patterns.md` §1). Column prefix indexes (`INDEX (name(20))`) cannot be covering and cannot serve `ORDER BY`.
- Comparing a `VARCHAR` column to an integer, or joining columns with different collations (`utf8mb4_general_ci` vs `utf8mb4_0900_ai_ci`), defeats the index. Check `SHOW CREATE TABLE` for both sides.
- `LIKE 'abc%'` uses a B-tree; `LIKE '%abc'` cannot. Infix search needs FULLTEXT or an application-side solution.
- Secondary indexes are updated on every write that touches an indexed column. Community MySQL's change buffer hides part of that cost; Aurora MySQL "doesn't use the InnoDB change buffer at all", so every index entry is a direct page write there. On a write-heavy table, count the existing indexes before adding one.

## 4. Statistics and histograms

```sql
ANALYZE TABLE orders;                                      -- refresh InnoDB persistent stats (cheap, online)
ANALYZE TABLE orders UPDATE HISTOGRAM ON status, region;   -- 8.0: histograms give the optimizer a real `filtered` for skewed, non-indexed columns
SHOW INDEX FROM orders;                                    -- Cardinality column: suspiciously low or NULL means stale stats
```

`innodb_stats_persistent_sample_pages` (default 20) can be too small for very large tables with skew; raise it per table with `ALTER TABLE ... STATS_SAMPLE_PAGES=...`.

## 5. Catalog queries

Statement digests (timers are picoseconds):

```sql
SELECT SCHEMA_NAME, COUNT_STAR AS calls,
       ROUND(SUM_TIMER_WAIT/1e12, 1) AS total_s, ROUND(AVG_TIMER_WAIT/1e9, 2) AS avg_ms,
       SUM_ROWS_EXAMINED AS rows_examined, SUM_ROWS_SENT AS rows_sent,
       LEFT(DIGEST_TEXT, 120) AS query
FROM performance_schema.events_statements_summary_by_digest
ORDER BY SUM_TIMER_WAIT DESC LIMIT 20;
```

Statements doing full scans, and unused or redundant indexes (the `sys` schema):

```sql
SELECT exec_count, total_latency, rows_examined, rows_sent, query
FROM sys.statements_with_full_table_scans ORDER BY total_latency DESC LIMIT 20;
SELECT * FROM sys.schema_unused_indexes;        -- since last restart; check replicas too
SELECT * FROM sys.schema_redundant_indexes;
```

Table sizes and row estimates:

```sql
SELECT table_name, table_rows, ROUND((data_length+index_length)/1024/1024) AS mb
FROM information_schema.tables WHERE table_schema = DATABASE() ORDER BY mb DESC LIMIT 20;
```

Lock waits and deadlocks are out of scope for this skill; `SHOW ENGINE INNODB STATUS` has a `LATEST DETECTED DEADLOCK` section when one occurred, and `innodb_lock_wait_timeout` defaults to 50 s.

## 6. Online DDL

```sql
SET SESSION lock_wait_timeout = 5;       -- metadata lock: fail fast instead of queueing behind a long transaction
ALTER TABLE orders ADD INDEX idx_orders_customer_status (customer_id, status),
      ALGORITHM=INPLACE, LOCK=NONE;      -- the server errors immediately if it cannot honour these
ANALYZE TABLE orders;
```

- Adding a secondary B-tree index is in-place with concurrent DML. FULLTEXT and SPATIAL indexes block concurrent DML. `ALGORITHM=INSTANT` cannot add an index.
- The build still reads the whole table and writes the index; on Aurora/RDS it adds replica lag and I/O. Schedule large builds.
- A long-running transaction holding a metadata lock on the table blocks the ALTER (and everything queued behind it); `lock_wait_timeout` protects you, and `SELECT * FROM sys.schema_table_lock_waits` shows the holder.
- `OPTIMIZE TABLE` rebuilds the table; online for InnoDB but heavy. Maintenance window.

## 7. Hints and session knobs (diagnostic only)

```sql
SELECT /*+ INDEX(o idx_orders_user_status) */ ... ;    -- 8.0.20+; equivalent to FORCE INDEX
SELECT /*+ JOIN_ORDER(u, o) */ ... ;
SELECT * FROM orders FORCE INDEX (idx_orders_user_status) WHERE ... ;
SET SESSION sort_buffer_size = 8*1024*1024;             -- for one session, to prove a filesort spill is the cost
```

A hint that fixes the query is evidence that statistics or the index set are wrong. Ship the index or the `ANALYZE`/histogram, and keep the hint only as a documented last resort for one statement.
