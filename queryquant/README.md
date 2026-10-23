# queryquant

Review the SQL queries in a codebase for performance problems, then dig into any one of them. You point it at a service or repo; it lists the problems worst first, with file and line; you pick one and get a fix you can paste into a ticket.

It is built for Java services that use JDBI SQL Objects and Liquibase changesets on Aurora MySQL. The deep dive also handles PostgreSQL and QuestDB plans. It never runs SQL against your database: when it needs a number from the database, it gives you the command to run and waits for the output.

## Usage

```
/queryquant                          # review the repo you're in
/queryquant services/billing        # review one service
/queryquant the database feels slow  # also start from the database's own slowest-statements list
```

Then:

```
look at 2
```

## What you get

**A review**, as a short numbered list in chat, worst first:

```
1. billing · InvoiceRepo.java:63 · listing invoices loads each invoice's lines one at a time, then one payments query per line · nested N+1 on a read path · from code
2. billing · CustomerRepo.java:172 · getCustomers() reads every customer, then 3 queries per customer · nested N+1 on an unbounded read · from code
+ 9 more one-row lookups while saving an invoice — minor unless saves carry 100+ items
```

Problems that hurt at any table size come first (N+1 loops, catch-all filters, unbounded reads, unchunked batches, offset paging), then missing indexes, which are marked "needs table size". Each line says whether it comes from the code or is confirmed by the database.

**A deep dive** on the one you pick, as a short card:

1. **What's wrong**: what the code and database do today, in everyday words.
2. **Fix**: one recommended change, ready to paste.
3. **Cost**: write overhead, disk, build time, side effects.
4. **Check it worked**: the command to run before and after, and what should change.
5. **Undo**: the rollback.

The evidence follows at the end.

## Safety

- It never connects to a database. You run every command it gives you.
- `EXPLAIN ANALYZE` runs the statement, so for writes it gives you plain `EXPLAIN`, or wraps the statement in `BEGIN` … `ROLLBACK` for a staging copy.
- Index changes use online DDL with a lock timeout: `ALGORITHM=INPLACE, LOCK=NONE` on MySQL, `CREATE INDEX CONCURRENTLY` on PostgreSQL.

## Configuration

An optional `.queryquant.json` at the root of the scanned path holds your conventions: paths to ignore, findings you accept and why, a tenant column that must lead every index, and small tables. See `references/scanner.md`.

## The scripts

Both read files only and need just Python 3.9 or later.

```bash
python3 scripts/scan_queries.py <path> --top 10            # review a service or repo
python3 scripts/scan_queries.py <path> --json scan.json    # every statement, table, index, and finding
python3 scripts/explain_check.py plan.json                 # triage a pasted plan
```

`explain_check.py` reads PostgreSQL `EXPLAIN` (JSON or text), pasted `pg_stat_statements` output, MySQL `EXPLAIN FORMAT=JSON` and `EXPLAIN ANALYZE` trees, and QuestDB `EXPLAIN`.

## Files

| Path | Contents |
|---|---|
| `SKILL.md` | The procedure the skill follows |
| `references/scanner.md` | What each scanner rule means, its blind spots, and the config file |
| `references/deep-dive.md` | The one-query procedure and the card |
| `references/mysql.md`, `postgres.md`, `questdb.md` | Engine glossaries, stats queries, lock-safe DDL |
| `references/patterns.md` | Index and rewrite patterns, each with when it does and does not apply |
| `scripts/scan_queries.py`, `scripts/qq_scan/` | The code scanner |
| `scripts/explain_check.py`, `scripts/qq_plan/` | The plan checker |
| `evals/` | Test cases for the skill itself; not installed |

## Tests

```bash
python3 -I scripts/test_scan_queries.py
python3 -I scripts/test_explain_check.py
```
