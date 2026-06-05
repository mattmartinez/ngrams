<objective>
Apply the same field changes (assignee, status, labels, priority) to every Jira
issue matching a JQL query, after the user confirms the matched set and the
planned changes.
</objective>

<required_reading>
- `workflows/search.md` — how JQL is constructed and pre-validated
</required_reading>

<process>

**Step 1: Build the JQL selector**

Same as `workflows/search.md` Step 1 — translate the user's natural language
into a JQL query that selects the issues to update. Bias toward narrow queries:
add `project =`, status filters, and label filters to avoid catching unrelated
issues. The script applies the same local validator as `search`, so unmatched
quotes and bare leading/trailing AND/OR are rejected before any API call.

**Step 2: Determine the field changes**

Pick whichever subset of these the user asked to change. Any combination is
allowed; at least one is required.

| Flag | Effect |
|------|--------|
| `--assignee ACCOUNT_ID` | Reassign all matched issues to this Jira account ID |
| `--status NAME` | Transition each issue to this status (case-insensitive) |
| `--labels "a,b,c"` | REPLACE the label set on each matched issue |
| `--priority NAME` | Set priority — Highest \| High \| Medium \| Low \| Lowest |

**Important semantics:**
- `--labels` REPLACES the label set. Pass `--labels ""` to clear all labels.
  There is no append/remove flag — if you need that, run a search first and
  build a per-issue diff manually.
- `--assignee` takes a Jira `accountId`, not an email address or display name.
  Look the account ID up via the Jira UI or `node jira-api.js whoami` for the
  current user.
- `--status` resolves to a transition per-issue. An issue whose current state
  does not allow the requested transition will fail individually; other
  matched issues continue.

**Step 3: Dry run first**

Always run without `--apply` first. The script lists matched issues and the
planned change set, then exits without writing anything.

```bash
source ~/.claude/jira.env
node ~/.claude/skills/jira/scripts/jira-api.js bulk-update \
  --jql '{JQL_QUERY}' \
  [--assignee {ACCOUNT_ID}] \
  [--status '{STATUS_NAME}'] \
  [--labels '{a,b,c}'] \
  [--priority '{PRIORITY_NAME}'] \
  [--max 20]
```

The output looks like:

```
Matched 3 issue(s):
  ACME-1234   Connection pool leak in auth service
  ACME-1240   Add timeout to HTTP client calls
  ACME-1255   Retry login on transient 5xx

Planned changes:
  labels   → backlog-cleanup
  priority → Low

Dry run — pass --apply to perform these updates.
```

**Step 4: Confirm with the user**

Show the matched set and planned changes verbatim, then ask:

```
About to update {N} issue(s) with the changes above. Apply? [yes / cancel]
```

- **yes / apply / y** → re-run the same command with `--apply` appended
- **cancel / no** → acknowledge and stop without writing
- **edit [instruction]** → tighten the JQL or change the field set, re-run dry
  run, loop back to confirmation

Never skip the dry-run + confirmation. Bulk updates are easy to over-broaden
and hard to undo.

**Step 5: Apply**

```bash
node ~/.claude/skills/jira/scripts/jira-api.js bulk-update \
  --jql '{JQL_QUERY}' \
  ...same flags... \
  --apply
```

The script writes per-issue:

```
✅ ACME-1234 updated
❌ ACME-1240 failed: no transition to "Done" available from current state
✅ ACME-1255 updated

Done. 2 updated, 1 failed.
```

Per-issue failures do not abort the run — every matched issue is attempted.
The command exits non-zero if any issue failed so the caller can detect
partial success.

**Step 6: Report**

Summarize:
```
Bulk update on {JQL}: {ok} updated, {failed} failed.
```

If anything failed, surface the per-issue error lines so the user can fix
those tickets individually. Common causes:
- Status transition not allowed from the current state
- Assignee accountId not valid for that project
- Label or priority value rejected by a project-specific validator

</process>

<success_criteria>
- The user sees the matched set + planned changes BEFORE any write
- Matched set looks correct (no over-broad JQL catching unrelated issues)
- Per-issue success/failure is reported by key
- Failed issues do not block the others
- Exit code reflects partial failures (non-zero on any failure)
</success_criteria>

<edge_cases>
- **0 matches** — script prints "No issues match the JQL" and exits 0; do not retry blindly
- **No field flags** — script errors with "At least one of --assignee, --status, --labels, --priority is required"
- **Malformed JQL** — local pre-validator catches unmatched quotes / bare AND/OR (see `workflows/search.md` Step 2); fix the query and rerun
- **Status transition unavailable** — happens per-issue; report it but continue
- **Many matches (>20)** — default `--max 20` caps the fetch; raise it deliberately, do not blanket-set high values
- **Clearing labels** — `--labels ""` is the explicit clear; an absent `--labels` flag means "do not touch labels"
</edge_cases>
