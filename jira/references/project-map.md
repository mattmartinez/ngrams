## Project Map

The file `~/.gsd/jira-projects.json` maps short human aliases to Jira project
keys. It is created during setup and can be edited manually at any time.

### Schema

```json
{
  "_comment": "Maps short aliases to Jira project keys. Add entries as needed.",
  "projects": [
    {
      "alias": "backend",
      "key": "BE",
      "name": "Backend"
    },
    {
      "alias": "frontend",
      "key": "FE",
      "name": "Frontend",
      "description": "Optional — shown when the agent asks which project to use"
    }
  ]
}
```

Fields:
- **alias** — what the user types (e.g. "backend", "frontend") — case-insensitive
- **key** — the actual Jira project key (e.g. "BE", "FE") — used in API calls
- **name** — human-readable project name shown in confirmations
- **description** — optional; helps the agent pick the right project when intent is ambiguous

### Resolution order

When creating a ticket, resolve the project key in this order:

1. **Explicit alias in user message** — "put this in the backend backlog", "file it as a BE ticket"
   → match against `alias` or `key` field (case-insensitive)

2. **CWD context** — if a `.gsd/PROJECT.md` exists in the current working directory,
   look for a line like:
   ```
   Jira Project: BE
   ```

3. **Ask the user** — if still ambiguous, list the projects from the map and ask:
   ```
   Which project?
   1. Backend (BE)
   2. Frontend (FE) — Optional description shown here
   ```

### Adding a project

```bash
# Open and edit directly
nano ~/.gsd/jira-projects.json
```

Or ask the agent: *"add a project — key BE, call it backend"*

### One project per team/board

Most setups have one Jira project key per team. If your instance has multiple
boards inside a single project (sprint board, backlog board, etc.), the project
key is the same for all of them — board selection happens in the Jira UI, not
at ticket creation time.
