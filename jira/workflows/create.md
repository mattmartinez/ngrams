<objective>
Turn user context — a conversation, a bug report, a code finding, a description — into a
well-structured Jira ticket, confirm it with the user, and post it.
</objective>

<required_reading>
- `references/adf.md` — ADF helpers used by the script
- `references/project-map.md` — how to resolve the right project key
</required_reading>

<process>

**Step 1: Extract ticket details from context**

From whatever the user provided (or from the current conversation), extract:

| Field | How to determine |
|-------|-----------------|
| `summary` | One-line description, ≤120 chars, imperative or noun-phrase |
| `issueType` | Bug / Story / Task / Spike — infer from context |
| `priority` | Highest / High / Medium / Low — infer from urgency language |
| `project` | See `references/project-map.md` for resolution order |
| `labels` | Infer from context (e.g. `security`, `performance`, `tech-debt`) |
| `description` | Full structured description — see Step 2 |

**Step 2: Write the description**

Build a description using ADF (see `references/adf.md`). Structure:

- **Summary paragraph** — 2-3 sentences on what the problem/story is
- **Details section** — specifics: affected files, error messages, reproduction steps, or acceptance criteria
- **Impact** — what breaks or what value this delivers
- **Suggested fix / approach** — optional, omit if not known

Keep it factual. Don't pad. If the user gave you a bug report or code finding, preserve the exact error messages and file paths.

**Step 3: Resolve the project**

```bash
cat ~/.gsd/jira-projects.json
```

Resolution order (from `references/project-map.md`):
1. User named a project/board explicitly → find alias in map
2. CWD has a `.gsd/PROJECT.md` with a `Jira Project:` field → use that key
3. Otherwise → show the project list and ask

**Step 4: Show the draft**

Present a compact preview:

```
📋 Ticket draft:

  Project:  {PROJECT_KEY}
  Type:     {issueType}
  Priority: {priority}
  Summary:  {summary}
  Labels:   {labels}

  Description preview:
  {first 3-4 lines of description prose}

Post this? [yes / edit / cancel]
```

**Step 5: Handle response**

- **yes / post / looks good / y** → go to Step 6
- **edit [instruction]** → apply the edit, re-show the draft, loop
- **cancel / no** → acknowledge and stop

**Step 6: Post the ticket**

```bash
source ~/.gsd/jira.env
node ~/.gsd/agent/skills/jira/scripts/jira-api.js create \
  --project '{PROJECT_KEY}' \
  --summary '{summary}' \
  --type '{issueType}' \
  --priority '{priority}' \
  --labels '{label1,label2}' \
  --description-file /tmp/jira-description-$$.json
```

The description JSON is written to a temp file first (avoids shell quoting hell).

**Step 7: Report result**

On success:
```
✅ Created {KEY}: {summary}
   {JIRA_BASE_URL}/browse/{KEY}
```

On failure: show the error message and offer to retry or save the draft locally.

</process>

<success_criteria>
- Ticket appears in Jira with the correct project, type, priority, and description
- The URL is shown so the user can open it immediately
- No credentials were echoed in output
</success_criteria>

<edge_cases>
- **No project context** — ask once, don't guess
- **issueType not found** — default to Task; note it in the confirmation
- **API 400** — usually an invalid field value; show the raw error to help diagnose
- **API 401** — credentials expired or wrong; direct user to run `/jira setup` again
- **Summary > 255 chars** — truncate with "..." and note it in the draft
</edge_cases>
