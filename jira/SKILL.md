---
name: jira
description: >
  Create, search, and manage Jira tickets from any terminal session. Use when
  the user says "make this a Jira ticket", "file a bug in Jira", "create a
  story for X", "put this in the backlog", "search Jira for Y", or any request
  to interact with Jira. Credentials are stored in ~/.gsd/jira.env and work
  across all projects.
---

<essential_principles>
## Credential Lookup

Credentials live at `~/.gsd/jira.env` — outside any repo, available from every terminal.

Load with:
```bash
source ~/.gsd/jira.env
# Provides: JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN
```

If the file does not exist, run: `workflows/setup.md`

The script at `scripts/jira-api.js` handles all Jira REST calls. It reads from
env vars — never hardcode credentials anywhere.

## Project Config

Each project maps a short alias to a Jira project key. The map lives at
`~/.gsd/jira-projects.json`. See `references/project-map.md` for format.

Resolve the right project key before creating a ticket:
1. If the user named a project/board explicitly → look it up in the map
2. If there's a `.gsd/PROJECT.md` in the current working directory → read the
   `Jira Project` field if present
3. Otherwise → ask the user which project (show a short list from the map)

## ADF (Atlassian Document Format)

Jira Cloud requires structured ADF for descriptions — plain strings are rejected.
See `references/adf.md` for the helper functions already built into the script.
Always build descriptions with those helpers, never with raw JSON.
</essential_principles>

<routing>
## Route Based on Intent

| User says | Workflow |
|-----------|----------|
| "make this a ticket", "file a bug", "create a story", "add to backlog" | `workflows/create.md` |
| "search Jira", "find tickets for", "what tickets exist for" | `workflows/search.md` |
| "set up Jira", credentials missing, `~/.gsd/jira.env` not found | `workflows/setup.md` |

When intent is clear, go straight to the workflow without asking.
When the user gives a blob of context (bug report, conversation, code review
output), treat it as a **create** request — extract the ticket details yourself.
</routing>
