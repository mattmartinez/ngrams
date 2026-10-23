# Sensor readings API — slow "recent readings for a device" endpoint

QuestDB 10.1.0 in prod, single node (16 vCPU, x86-64), fed by ILP at ~1,500 rows/s average (peaks ~12k rows/s) across ~12,000 devices.
Prod table `readings`: ~55 billion rows, 14 months of data (PARTITION BY DAY, ~130 M rows per partition).
plan.txt was captured on the staging copy: same schema, 19 M rows over 22 days (2026-10-01 to 2026-10-22), same 12,000 devices.

```sql
CREATE TABLE readings (
    ts          TIMESTAMP,
    ingested_at TIMESTAMP,
    device      SYMBOL,
    site        SYMBOL,
    value       DOUBLE,
    quality     INT
) TIMESTAMP(ts) PARTITION BY DAY WAL;
```

The endpoint query (ts is the designated timestamp; `ingested_at` is when the row reached us):

```sql
SELECT ts, value, quality
FROM readings
WHERE ingested_at >= '2026-10-20T00:00:00Z' AND device = 'QOWTVBQNHS'
ORDER BY ts DESC
LIMIT 500;
```

Wall time from the API in prod: 38–52 s warm. Target: under 1 s. `query_activity()` shows it at state `running` with memory_used climbing past 2 GB.
On the staging copy the same query runs in 42 ms warm; the device has 1,583 rows there.
I saved `EXPLAIN` output to plan.txt. There is no index on `device`. Ingestion must not slow down; we are already close to the O3 lag ceiling at peak.
