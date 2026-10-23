# queryquant — QuestDB reference

Read this when the engine is QuestDB. It speaks the PostgreSQL wire protocol, but it is a time-series engine with its own SQL and storage model, and almost nothing in `postgres.md` applies. Facts below come from the QuestDB docs (10.0.x) and source, checked in October 2026.

## Contents

1. What is different
2. Capturing a plan
3. Reading EXPLAIN
4. Designated timestamp and partitions
5. SYMBOL columns and indexes
6. Query shapes
7. Operations and measurement
8. Ingestion side effects

## 1. What is different

- Storage is columnar, append-oriented, partitioned by time. There is no B-tree. The only index type is on `SYMBOL` columns (bitmap or posting). "Index support for other types will be added over time."
- Most speed comes from the **designated timestamp**: a predicate on it becomes an interval scan that skips whole partitions. A predicate on any other timestamp column is a full scan.
- `EXPLAIN` exists; `EXPLAIN ANALYZE` does not. There is no `pg_stat_statements`, no `CREATE INDEX CONCURRENTLY`, no `VACUUM`.
- Not supported over PG wire: `DELETE`, `HAVING`, `OFFSET`, `DISTINCT ON`, `ON CONFLICT`, correlated subqueries in `WHERE`.
- Filters are JIT-compiled to native code when they qualify (§6), and scans, filters, and group-bys run in parallel across the shared query workers.

## 2. Capturing a plan

```sql
EXPLAIN SELECT ...;      -- also works for INSERT, UPDATE, CREATE TABLE AS
```

Returns one text column, `QUERY PLAN`, as an indented tree. It never runs the statement, so there are no actual times or row counts. Measure the query itself from the client (wall time, rows), run it at least twice to separate cold from warm reads, and watch it in `query_activity()` (§7). Save the EXPLAIN text to a file; the checker recognises it.

## 3. Reading EXPLAIN

Example from the docs (a filter that is JIT-compiled and runs on up to 47 workers, over a full scan of `trades`):

```
Async JIT Filter workers: 47
  filter: 100.0<amount [pre-touch]
    PageFrame
        Row forward scan
        Frame forward scan on: trades
```

The same table with a predicate on the designated timestamp becomes an interval scan:

```
Update table: trades
  VirtualRecord
    functions: [0]
      PageFrame
        Row forward scan
        Interval forward scan on: trades
          intervals: [static=[1668124800000000,1668211199999999]
```

| Node | Meaning | Worry when |
|---|---|---|
| `Frame forward scan on: t` / `Frame backward scan` | Reads every partition and row | The query has a time range you could express on the designated timestamp, or the table is large and no interval or index scan appears |
| `Interval forward scan on: t` + `intervals:` | Partition and row-range pruning from a designated-timestamp predicate | Never; this is what you want |
| `Index forward scan` / `Index backward scan` | Scans row ids for one symbol value via its index | The symbol has very few distinct values (an index then reads most of the table anyway) |
| `Async JIT Filter workers: N` | Filter compiled to native code, parallel | `workers` is an upper bound, not actual parallelism |
| `Async Filter workers: N` | Parallel filter, **not** JIT-compiled | A predicate used a function, `in()` on a symbol, a `BETWEEN` on a non-designated timestamp, or an unsupported type (§6); rewrite to qualify for JIT |
| `Filter` | Single-threaded filter | Large input |
| `Sort`, `Sort light`, `Encode sort`, `Encode sort light` | In-memory sort; `lo`/`hi` means the buffer is capped by a LIMIT | Big inputs: "Ordering data requires holding it in RAM". `ORDER BY ts DESC` on the designated timestamp shows as `Row backward scan` + `Frame backward scan` with no sort node (verified on 10.1.0) |
| `GroupBy vectorized: true` / `Async Group By` / `Async JIT Group By` | Aggregation, vectorised or parallel | Single-threaded `GroupBy` on a large input |
| `SampleBy` | `SAMPLE BY` bucketing; no `fill:` line means `fill(none)` | |
| `LatestByAllIndexed` and other `LatestBy*` | `LATEST ON` | Multi-column keys (full scan, §6) |
| `Hash Join` / `Hash Join Light` | Equality join | Both sides large; consider `ASOF JOIN` for time alignment |
| `AsOf Join Fast` / `Lt Join Light` and heavy variants | Time-aligned joins | Missing `TOLERANCE` on a sparse right side |
| `Limit`, or a `limit: N` line under `Async JIT Filter` | `LIMIT n` / `LIMIT -n`; with a filter the limit is pushed into the filter node and the scan stops early | |
| `PageFrame`, `Row forward scan`, `VirtualRecord`, `SelectedRecord` (the docs write `Selected Record`), `Count`, `Hash`, `Window`, `CachedWindow`, `CoveringIndex`, `PostingIndex` | Structural nodes | |

Join and sort nodes come in "light" and "heavy" variants; heavy ones materialise more.

## 4. Designated timestamp and partitions

- The predicate must be on the designated timestamp column for an interval scan: "Timestamp predicates execute first" and "Entire partitions outside the time range are skipped". Any other timestamp column: "full scan, but still works".
- Filter syntax: `ts IN '2026-10'` (any ISO-8601 prefix), `ts IN '2026-10-20;1d'` (modifiers `'timestamp;modifier[;interval;repetition]'`), `ts BETWEEN a AND b` (both bounds inclusive), `ts >= a AND ts < b`.
- `PARTITION BY HOUR | DAY | WEEK | MONTH | YEAR | NONE`. Tables created via SQL default to `NONE`; tables auto-created by ILP default to `DAY`. Target 30 to 80 million rows per partition. Changing partitioning requires recreating the table (`CREATE TABLE ... AS SELECT`).
- An unpartitioned or badly partitioned large table has no pruning to offer; that is a data-design fix, not a query fix.

## 5. SYMBOL columns and indexes

```sql
CREATE TABLE sensors (ts TIMESTAMP, device SYMBOL, site SYMBOL INDEX, value DOUBLE) TIMESTAMP(ts) PARTITION BY DAY;
ALTER TABLE sensors ALTER COLUMN device ADD INDEX;                  -- bitmap index, added after the fact
ALTER TABLE sensors ALTER COLUMN device DROP INDEX;
ALTER TABLE sensors ALTER COLUMN device ADD INDEX TYPE POSTING;     -- posting index
```

- "Adding an index is an atomic, non-blocking, and non-waiting operation."
- Write cost: for the indexed column, "any write will now require two write operations, and therefore take twice as long." Storage is roughly 15 bytes per value for bitmap and about 1 byte for posting.
- The index pays off for selective symbol values (one device out of thousands). The docs give no cardinality threshold; if a value matches a large share of rows, the `Index forward scan` reads most row ids anyway and a JIT filter over the interval scan is as good. Test both.
- Symbol `CAPACITY` is obsolete per the symbol page (the config page still lists `cairo.default.symbol.capacity`, default 256). For 10M+ distinct values use `NOCACHE`.

## 6. Query shapes

- **Always narrow by the designated timestamp first.** Then add symbol equality, then everything else.
- **`LATEST ON ts PARTITION BY sym`**: with a single symbol key QuestDB can "stop scanning table contents once the latest entry has been found"; with multiple partition columns "this will result in a full table scan." `WHERE` is applied before `LATEST ON` unless you wrap `LATEST ON` in a subquery, which changes both the result and the cost.
- **`SAMPLE BY`**: `FILL(NONE | NULL | PREV | PREV(col) | LINEAR | constant)`. `ALIGN TO CALENDAR` is the default since 7.4.0.
- **`ASOF JOIN`** "requires tables or subqueries to be ordered by time". `TOLERANCE` "allows these scans to terminate early" when the right side is sparse. `LT JOIN` excludes exact timestamp matches; `SPLICE JOIN` is the full outer form.
- **Latest rows**: `ORDER BY ts DESC LIMIT n` on the designated timestamp is a backward scan with no sort; `LIMIT -n` returns the last n rows.
- **JIT filter** requires all of: column types among `BOOLEAN, BYTE, GEOBYTE, SHORT, GEOSHORT, CHAR, INT, GEOINT, SYMBOL, FLOAT, LONG, GEOLONG, DATE, TIMESTAMP, DOUBLE, UUID`; only `+ - * /` arithmetic; no SQL functions; no `in()` on symbols; no `BETWEEN` on a non-designated timestamp; an x86-64 CPU; `cairo.sql.jit.mode=on` (default) and `cairo.sql.parallel.filter.enabled=true` (default). A plan showing `Async Filter` instead of `Async JIT Filter` means one of these failed; rewrite the predicate (e.g. move the function to the constant side).
- `ORDER BY` on anything other than the designated timestamp materialises the result in RAM; put a `LIMIT` on it or pre-filter by time.

## 7. Operations and measurement

- `SELECT * FROM query_activity();` lists running queries with `query_id`, `state`, and `memory_used`. `CANCEL QUERY <query_id>;` stops one.
- Parallelism: `cairo.sql.parallel.filter.enabled` (default `true`; JIT only works when it is on), `cairo.sql.parallel.groupby.enabled` (default `true`, needs at least 4 shared query workers). Worker pools: `shared.query.worker.count`, `shared.network.worker.count`, `shared.write.worker.count`, each defaulting to roughly the CPU count.
- There is no statement-stats table and no documented slow-query log. Measure from the client and from `query_activity()` while the query runs; compare wall time before and after a change with a warm cache.

## 8. Ingestion side effects

- WAL tables are the default and "require partitioning". `DEDUP` needs a WAL table and "the designated timestamp must be included in UPSERT KEYS"; it costs more when many rows share a timestamp.
- Out-of-order (O3) writes rewrite partition data: "Too few large partitions = more data rewritten when out-of-order rows arrive." Relevant settings: `cairo.o3.max.lag` (10 minutes), `cairo.o3.min.lag` (1 second), `cairo.o3.partition.split.min.size` (50MB), `cairo.o3.last.partition.max.splits` (20), `cairo.max.uncommitted.rows` (500000).
- Adding a symbol index doubles the write work for that column (§5); on a high-rate ingest table, check ingestion latency after adding one.
