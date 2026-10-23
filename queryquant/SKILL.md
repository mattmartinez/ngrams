---
name: queryquant
description: Review the SQL queries in a codebase for performance problems, then dig into any one of them. Scans a service or repo (Java with JDBI SQL Objects and Liquibase changesets first; Aurora MySQL first, PostgreSQL and QuestDB in the deep dive), lists N+1 loops, missing indexes, unbounded reads, catch-all filters, offset paging and unchunked batches ranked worst first with file and line, and on request turns one finding into a fix with an EXPLAIN the user runs. Use whenever the user suspects the database is slow because of queries, wants to optimize or audit queries, asks "review the queries in this service", asks which index a query needs, pastes an EXPLAIN or query plan, or pastes pg_stat_statements or performance_schema output, even if they never say "optimize". Not for searching logs, dashboards, or monitoring tools for slow statements, not for deadlocks or lock waits, and not for profiling application code that is not database access.
argument-hint: "[path to a service or repo — default: the current repo] [optional: 'the database feels slow']"
---

# queryquant

The user suspects queries are making the database slow, or wants them reviewed. The job has two steps: **review** the queries in the code and list the problems worst first, then **deep dive** into whichever one the user picks. The skill never runs SQL against a database; when a database number is needed, it hands the user the command and waits for the paste.

## Review

1. **Scope.** Use the path the user gave, or the current repo. If the user says the database itself feels slow, also do step 6 before the list.
2. **Scan.** Run the scanner; it reads every SQL statement in the code, each service's Liquibase changesets, and the indexes they define, then applies its rules.

   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/scan_queries.py <path> --top 10                 # ranked list
   python3 ${CLAUDE_SKILL_DIR}/scripts/scan_queries.py <path> --json /tmp/qq-scan.json  # all facts
   ```

   It reads `.queryquant.json` at the repo root if present (paths to ignore, accepted exceptions, a tenant column that must lead every index, small tables). Run it, do not read its source. `references/scanner.md` explains each rule, what the scanner cannot see, and the config file.
3. **Verify before claiming.** Open the cited line for every finding you are about to show, and read enough around it to be sure: the loop really runs per row, the filter really has no index, the list really has no bound. Follow a helper method one level if the finding depends on it. Drop a finding that does not hold, and say "not checked" for any you could not verify rather than guessing.
4. **Rank.** Worst first: anything confirmed by database stats (step 6); then problems that hurt at any table size (N+1 loops, nested first and read paths before save paths; catch-all filters; unbounded reads; unchunked batches; offset paging); then missing indexes, marked "needs table size" because the code cannot show row counts. Small framework tables (scheduler, changelog) go last. A comment in the code that reports slowness (a ticket number, "30 seconds", "timeouts") moves a finding up and should be quoted.
5. **Reply in chat** with at most 10 lines, one per finding, then up to 5 lines grouping the rest:

   ```
   1. billing · InvoiceRepo.java:63 · listing invoices loads each invoice's lines one at a time, then one payments query per line · nested N+1 on a read path · from code ("batch this, 30 s on big accounts")
   2. …
   + 7 more one-row lookups while saving an invoice (InvoiceService) — minor unless saves carry 100+ items
   ```

   Each line: number, service, file:line, what the code does in everyday words, the problem, and the evidence level: `from code`, `confirmed by EXPLAIN`, or `confirmed by DB stats`. End with one sentence offering the deep dive, for example "Say 'look at 1' to get the EXPLAIN and a fix." Write no files unless asked.
6. **"The database feels slow."** Hand the user the top-statements query for their engine (MySQL: the `performance_schema.events_statements_summary_by_digest` query in `references/mysql.md` §5; PostgreSQL: the `pg_stat_statements` query in `references/postgres.md` §5) to run on a reader and paste back. Match each top statement to its place in the code by its tables and filter columns, using the scan's `--json` facts, and rank those first as `confirmed by DB stats`.

## Deep dive ("look at 3", a pasted plan, or one slow statement)

Read `references/deep-dive.md` and follow it. In short: for a slow statement, write the `EXPLAIN` for it and ask the user to run it on a staging reader (never `EXPLAIN ANALYZE` of a write on prod), run `scripts/explain_check.py` on the pasted plan, and choose the cheapest fix that changes the costly node. For an N+1, an unbounded read, or an unchunked batch, skip the plan: write the bulk replacement and give the call-count check instead. Either way, answer with the short ticket card: What's wrong, Fix, Cost, Check it worked, Undo, then the evidence. Every index change uses lock-safe DDL.

## Writing for the user

Plain words and short sentences. Say what the code or database does in everyday terms ("runs one query per user", "reads the whole table"), not in plan field names; if a field name is needed, say what it means in the same sentence. Lead with the answer. Never present a guess from the code as a measured fact, and keep staging numbers apart from prod numbers.

## Files

- `scripts/scan_queries.py` — the code scanner; run it, do not read it (`--help` for options)
- `scripts/explain_check.py` — plan triage for the deep dive; run it, do not read it
- `references/scanner.md` — what each scanner rule means, its blind spots, and `.queryquant.json`
- `references/deep-dive.md` — the one-query procedure and the ticket card
- `references/mysql.md`, `references/postgres.md`, `references/questdb.md` — engine glossaries, stats queries, lock-safe DDL
- `references/patterns.md` — index and rewrite patterns, each with when it applies and when it does not
