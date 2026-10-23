# queryquant — index and rewrite patterns

Each pattern says when it applies and when it does not. Apply one only after the plan shows the symptom; see SKILL.md §3. Examples are PostgreSQL unless marked; MySQL differences are in `mysql.md`.

## Contents

1. Composite index column order
2. Partial, expression, and covering indexes
3. Sargable predicates
4. `OR`, `IN`, `NOT IN`, `EXISTS`
5. N+1 and batch loading
6. Pagination without `OFFSET`
7. Counting
8. Correlated subqueries and `DISTINCT` fan-out
9. Batch writes
10. Materialized views and partitioning
11. Things that are not optimizations

## 1. Composite index column order

Symptom: a multi-column index exists but the plan still filters most rows, or `filtered` is low (MySQL).

Rule: equality columns first, then the one range column, then `ORDER BY` columns. A B-tree can use the leading columns for equality, one range after them, and nothing after the range for filtering.

```sql
-- WHERE user_id = $1 AND status = $2 AND created_at > $3 ORDER BY created_at DESC
CREATE INDEX CONCURRENTLY idx_orders_user_status_created
    ON orders (user_id, status, created_at DESC);
```

Does not apply: when the leading column is not in the predicate (the index is then a full index scan at best), or when a lower-cardinality column is placed first "because it is smaller" (cardinality order matters less than predicate shape).

## 2. Partial, expression, and covering indexes

**Partial**: the predicate is fixed and selects a small slice.

```sql
CREATE INDEX CONCURRENTLY idx_jobs_pending ON jobs (created_at) WHERE status = 'pending';
```
The query must contain the same predicate literally (`status = 'pending'`), or the planner will not match it. Parameterized `status = $1` does not match.

**Expression**: the predicate wraps the column in a function.

```sql
CREATE INDEX CONCURRENTLY idx_users_email_lower ON users (lower(email));
-- query must use lower(email) = lower($1)
```
Prefer storing normalized data when the app controls writes; an expression index costs the function on every write.

**Covering**: the plan is an Index Scan with heap fetches for one or two extra columns, or MySQL shows `using_index: false` on a hot lookup.

```sql
CREATE INDEX CONCURRENTLY idx_users_email_inc ON users (email) INCLUDE (region, created_at);
```
Index-only scans still need the visibility map set; see `postgres.md`. Every included column adds write cost.

## 3. Sargable predicates

Symptom: an index on the column exists but the plan does a seq scan or `key: null` with the index in `possible_keys`.

| Not sargable | Sargable |
|---|---|
| `date_trunc('day', created_at) = $1` | `created_at >= $1 AND created_at < $1 + interval '1 day'` |
| `created_at::date = $1` | same range form |
| `user_id = '123'` where `user_id` is `int` (MySQL: string vs int compare, or mismatched collation on join columns) | bind the right type |
| `name LIKE '%smith'` | trigram index (`pg_trgm` GIN) for infix search; `LIKE 'smith%'` with `text_pattern_ops` or `C` collation |
| `coalesce(deleted_at, '9999-01-01') > now()` | `deleted_at IS NULL OR deleted_at > now()` with a partial index on `deleted_at IS NULL` |
| `extract(year from created_at) = 2026` | range on `created_at` |

## 4. `OR`, `IN`, `NOT IN`, `EXISTS`

- `OR` across different columns: Postgres can use a BitmapOr of two indexes; MySQL can use `index_merge`. If the plan still scans, rewrite as `UNION ALL` of two indexable branches (dedupe only if the branches overlap).
- `IN (list)`: fine; Postgres plans it as `= ANY(array)`. Very long lists (thousands) are better as a join to a `VALUES` or temp table.
- `NOT IN (subquery)`: if any value in the subquery is `NULL`, the result is empty, and the planner cannot use an anti-join. Use `NOT EXISTS`.

```sql
SELECT u.id FROM users u
WHERE NOT EXISTS (SELECT 1 FROM orders o WHERE o.user_id = u.id);
```
- `EXISTS` vs `IN`: both plan as semi-joins in modern Postgres and MySQL 8.0; pick the readable one. The exception is `NOT IN`, above.

## 5. N+1 and batch loading

Symptom: `pg_stat_statements` or the digest table shows one cheap statement with a huge call count.

Load the parent set, then one query for all children with an array or list parameter:

```sql
SELECT * FROM orders WHERE user_id = ANY($1);      -- Postgres, $1 = int[]
SELECT * FROM orders WHERE user_id IN (?, ?, ?);   -- MySQL: expand placeholders
```
Group rows by `user_id` in the application. A `JOIN` that returns parent columns repeated per child row moves the fan-out to the network; use it when the child count per parent is small.

## 6. Pagination without `OFFSET`

Symptom: the plan reads and discards `OFFSET` rows; latency grows with page number.

Keyset (cursor) pagination on a unique, ordered key:

```sql
SELECT * FROM events
WHERE (created_at, id) < ($last_created_at, $last_id)
ORDER BY created_at DESC, id DESC
LIMIT 50;
CREATE INDEX CONCURRENTLY idx_events_created_id ON events (created_at DESC, id DESC);
```
MySQL 8.0 supports the row-value comparison but may not use the index for it; write it out: `created_at < ? OR (created_at = ? AND id < ?)`.

Does not apply: when the UI needs "jump to page 37". Then cap the depth or precompute.

## 7. Counting

- `COUNT(*)` over a whole large table: use the estimate (`pg_class.reltuples`, `information_schema.tables.table_rows`) unless exactness is required.
- Filtered counts: an index on the filter columns lets Postgres do an index-only scan; in MySQL a covering secondary index is smaller than the clustered PK.
- Avoid `COUNT(DISTINCT x)` on large sets in the request path; maintain a counter or use `approx_count_distinct` style extensions where available.

## 8. Correlated subqueries and `DISTINCT` fan-out

Correlated subquery in the select list runs once per outer row. Replace with a join and aggregation:

```sql
SELECT u.id, u.email, COUNT(o.id) AS order_count
FROM users u LEFT JOIN orders o ON o.user_id = u.id
GROUP BY u.id;    -- u.email is functionally dependent on the PK in both engines
```

If a `DISTINCT` was added to make row counts "right", the join fans out. Aggregate the child side first in a subquery or CTE, then join one row per parent. The `DISTINCT` is the symptom, not the fix.

## 9. Batch writes

- Multi-row `INSERT ... VALUES (...), (...)`; Postgres `COPY` or MySQL `LOAD DATA` for bulk loads.
- Batch `UPDATE` from a value list rather than a loop:

```sql
UPDATE orders AS o SET status = v.status
FROM (VALUES (1, 'shipped'), (2, 'cancelled')) AS v(id, status)
WHERE o.id = v.id;
```
MySQL: `INSERT ... ON DUPLICATE KEY UPDATE`, or a temp table joined in the `UPDATE`.
- Keep batches at a few thousand rows so one statement does not hold locks or bloat the WAL/binlog for minutes. Delete in chunks by primary key range for the same reason.

## 10. Materialized views and partitioning

**Materialized view**: an aggregate that is read often and may be minutes stale.

```sql
CREATE MATERIALIZED VIEW user_order_summary AS ...;
CREATE UNIQUE INDEX ON user_order_summary (user_id);   -- required for CONCURRENTLY
REFRESH MATERIALIZED VIEW CONCURRENTLY user_order_summary;
```
MySQL has no materialized views; use a summary table maintained by the app or a scheduled job.

**Partitioning**: applies when queries always filter on the partition key (usually time) and old partitions get dropped. It does not speed up queries that do not include the key, and in Postgres the primary key must include the partition key. Treat it as data design, not a per-query fix.

## 11. Things that are not optimizations

- `SELECT *` vs explicit columns: matters only when it blocks an index-only scan or moves wide rows over the network. It does not change the plan otherwise.
- `FROM a, b WHERE a.id = b.id` vs `JOIN`: identical to the planner.
- Pre-filtering in a subquery: the planner already pushes predicates down.
- Moving a `HAVING` predicate to `WHERE`: only valid when the predicate does not involve the aggregate; otherwise it changes the result.
- Window function plus `DISTINCT ON` instead of `GROUP BY` for per-group counts: strictly more work.
- Session planner toggles (`enable_nestloop = off`, `optimizer_switch`): diagnostic only. If they produce a better plan, that is evidence of a statistics or cost-parameter problem; fix that.
