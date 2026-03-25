<objective>
Search Jira for tickets matching a query and present results clearly.
</objective>

<process>

**Step 1: Build the JQL query**

From the user's natural language, construct a JQL query:

| Intent | JQL pattern |
|--------|-------------|
| "find tickets for X" | `text ~ "X" ORDER BY updated DESC` |
| "open bugs in [project]" | `project = KEY AND issuetype = Bug AND statusCategory != Done` |
| "tickets assigned to me" | `assignee = currentUser() ORDER BY updated DESC` |
| "recent tickets in [project]" | `project = KEY ORDER BY created DESC` |
| "tickets with label X" | `labels = "X" ORDER BY updated DESC` |
| "high priority open" | `priority in (Highest, High) AND statusCategory != Done` |

Combine conditions with AND. Always add `ORDER BY updated DESC` unless user specifies otherwise.
Limit to 20 results unless user asks for more.

**Step 2: Run the search**

Resolve the project key from `~/.gsd/jira-projects.json` if needed (same resolution as create workflow).

```bash
source ~/.gsd/jira.env
node ~/.gsd/agent/skills/jira/scripts/jira-api.js search \
  --jql '{JQL_QUERY}' \
  --max 20
```

**Step 3: Display results**

```
🔍 Jira search: {original user query}
   JQL: {jql}

Found {N} tickets:

KEY        TYPE    PRI   STATUS          SUMMARY
─────────────────────────────────────────────────────────────────
CD-1234    Bug     High  In Progress     Connection pool leak in auth service
CD-1235    Task    Med   To Do           Add timeout to HTTP client calls
...

{JIRA_BASE_URL}/issues/?jql={url-encoded-jql}
```

If 0 results, say so clearly and suggest broadening the query.

**Step 4: Optional follow-up**

After showing results, offer:
- "Open any of these?" → show the URL
- "Create a related ticket?" → run the create workflow

</process>

<success_criteria>
- Results shown in a scannable table
- JQL is visible so user can refine manually in the browser
- Zero-result case is handled gracefully
</success_criteria>
