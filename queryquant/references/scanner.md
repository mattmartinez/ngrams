# queryquant — the code scanner

Read this when a finding needs explaining, when the scan looks wrong, or when setting up `.queryquant.json`.
`scripts/scan_queries.py` reads code and schema files only. It never connects to a database.

## What it reads

- **Services.** A folder with a Liquibase master (`migrations.xml`, `changelog.xml`, or similar) or a
  `build.gradle` with `src/main/java`. It skips `build/`, `bin/`, `target/`, `src/test`,
  `src/scenarioTest`, and Groovy files.
- **Schema.** Only the changesets the Liquibase master actually includes, in order. Folders often hold
  archived files that never run, and those are ignored. It parses MySQL DDL: `CREATE TABLE` (including
  one-line definitions), `ALTER TABLE … ADD INDEX/KEY`, `CREATE INDEX`, `DROP INDEX`, and renames. It
  also models InnoDB behaviour: secondary indexes carry the primary key, and a foreign key with no index
  gets one.
- **Statements.** JDBI SQL Object annotations (`@SqlQuery`, `@SqlUpdate`, `@SqlBatch`) with text blocks,
  `"…" + "…"` concatenation, or same-file constants; fluent `handle.createQuery/createUpdate("…")`; and
  JDBC `prepareStatement("…")`. Each statement keeps its file, line, DAO, method, return type, and
  annotations.

## Rules

| Rule | Fires when | Why it matters |
|---|---|---|
| `qq.n-plus-one` | A DAO read runs inside a `for`/`while` loop or a stream `map/forEach/flatMap`, directly or through same-class helper methods (up to three levels). `nested` when the reached code loops again. | One query per row. The cost grows with the data, even when every query is fast and indexed. |
| `qq.catch-all-filter` | `(:x IS NULL OR col = :x)` and variants such as `COALESCE(:x, col) = col`. | MySQL cannot pick one index plan for "maybe filter, maybe not", so it often scans. |
| `qq.unbounded-list` | A `List`/`Stream` read with no `LIMIT`, and no filter on a unique key or a parent key (`*_id`, `*_guid`, `*_uuid`). | It returns more rows every month the table grows. |
| `qq.unchunked-batch` | `@SqlBatch` without `@BatchChunkSize`. | With `rewriteBatchedStatements=true` the whole batch becomes one huge statement. |
| `qq.offset-paging` | `LIMIT … OFFSET …` or `LIMIT a, b` with a bind. | Every page reads and throws away all earlier rows. Keyset paging avoids it. |
| `qq.no-index-for-filter` | No index or primary key starts with any of the statement's filter or join columns. | Likely a full table scan. Marked "needs table size": harmless on a small table. |
| `qq.no-index-for-sort` | A sorted top-N or list read where no index starts with the filter columns followed by the sort columns. | Likely a sort of every matching row. Also "needs table size". |
| `qq.tenant-column-not-leading` | Config only: an index on a table that has the tenant column does not start with it. | Every query filters by tenant first, so the index is not used as intended. |

**Path kind.** Each finding is tagged `read`, `write`, `job`, or `unknown`, based on the JAX-RS
annotation, the method name (get/find/list/search vs save/insert/update/delete), and the class name
(Task/Job/Scheduler). Read paths rank above write paths. A one-row lookup per item while saving is
usually validation of a few items and ranks low.

**Small tables.** Quartz (`qrtz_*`), `databasechangelog*`, and `flyway_schema_history` are treated as
small. Every finding on them is low severity.

## Blind spots

Say these out loud when they matter, rather than guessing:
- **Table sizes.** It has no row counts, so every index finding needs one before it is called a problem.
- **Calls across classes.** It follows helper calls only inside the same class. A service that loops and
  calls another service, which then calls a DAO, is not seen.
- **Dynamic SQL.** Text built at runtime, `@Define` placeholders, SQL passed in as a parameter, and
  constants from other files are listed as "not analysed".
- **Call frequency.** It cannot tell how often an endpoint is called. A nested N+1 on an admin page used
  once a week matters less than a small one on a hot path. Ask, or use database stats.
- **Other stores.** Statements against QuestDB, Snowflake, or other non-MySQL stores are extracted but
  the index rules assume MySQL.

## `.queryquant.json`

Optional, at the root of the scanned path (or pass `--config FILE`). Company conventions live here so
the skill stays generic.

```json
{
  "ignore": ["**/legacy-prototype/**"],
  "accept": [{"rule": "qq.offset-paging", "path": "**/admin/**", "reason": "admin page, small tables"}],
  "leading_column": {"column": "tenant_id", "severity": "medium"},
  "small_tables": ["country_*", "currency"]
}
```

- `ignore`: extra paths to skip.
- `accept`: findings to drop, each with the reason. The scan prints how many were accepted.
- `leading_column`: turns on `qq.tenant-column-not-leading`.
- `small_tables`: extra tables to treat as small.
