# Nightly purge — context

- PostgreSQL 15.6 on RDS, **production primary**. I only have a read-only role on prod during the day; the purge runs from a cron job with the app role at 02:00 CT.
- Table `events`: ~410,000,000 rows, 190 GB including indexes. Append-only except for this purge.
- The job: `DELETE FROM events WHERE created_at < now() - interval '90 days';` — last night it ran 41 minutes, deleted 4.9 M rows, and the app saw lock waits and elevated replica lag for the whole window.
- The plan below was captured on our staging copy (PostgreSQL 16, a 1,000,000-row sample of `events` with the same indexes); on staging the predicate matches ~11.6k rows, the same ~1.2% as prod.

```sql
CREATE TABLE events (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id  bigint NOT NULL,
    kind        text NOT NULL,
    payload     jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_events_account_created ON events (account_id, created_at);
CREATE INDEX idx_events_kind ON events (kind);
CREATE INDEX idx_events_payload ON events USING gin (payload);
```

What I ran this morning on staging (read-only, no ANALYZE):

```
EXPLAIN (FORMAT JSON) DELETE FROM events WHERE created_at < now() - interval '90 days';
```

```json
[
  {
    "Plan": {
      "Node Type": "ModifyTable",
      "Operation": "Delete",
      "Parallel Aware": false,
      "Async Capable": false,
      "Relation Name": "events",
      "Alias": "events",
      "Startup Cost": 0.00,
      "Total Cost": 28991.00,
      "Plan Rows": 0,
      "Plan Width": 0,
      "Plans": [
        {
          "Node Type": "Seq Scan",
          "Parent Relationship": "Outer",
          "Parallel Aware": false,
          "Async Capable": false,
          "Relation Name": "events",
          "Alias": "events",
          "Startup Cost": 0.00,
          "Total Cost": 28991.00,
          "Plan Rows": 11642,
          "Plan Width": 6,
          "Filter": "(created_at < (now() - '90 days'::interval))"
        }
      ]
    }
  }
]
```
