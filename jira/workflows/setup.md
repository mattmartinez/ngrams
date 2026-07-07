<objective>
Walk through one-time credential setup so the Jira skill works from every terminal.
</objective>

<process>

**Step 1: Collect credentials**

Do NOT ask the user to paste the API token into the conversation — it would be
stored in plaintext in the session history. Instead, print the heredoc below and
have the **user** run it in their own terminal, filling in the three values. The
`export` keyword is mandatory: the script runs as a bash subprocess and reads
`process.env`, so plain `KEY=value` (no export) would not reach it.

```bash
cat > ~/.claude/jira.env <<'EOF'
export JIRA_BASE_URL=
export JIRA_EMAIL=
export JIRA_API_TOKEN=
EOF
chmod 600 ~/.claude/jira.env
```

Per-key guidance to relay to the user:
- **JIRA_BASE_URL** — the base URL of the Jira instance, everything up to but not
  including `/browse` or `/jira`. Example: `https://acme.atlassian.net`
- **JIRA_EMAIL** — the email address used to log into Jira.
- **JIRA_API_TOKEN** — create one at
  `https://id.atlassian.com/manage-profile/security/api-tokens` → "Create API
  token" → name it e.g. `agent-cli` and copy it (starts with `ATATT3x...`).

The token is never seen by Claude — Step 4's `whoami` verifies it without it
ever entering the conversation.

**Step 2: Create project map**

Check if `~/.claude/jira-projects.json` already exists:
```bash
cat ~/.claude/jira-projects.json 2>/dev/null
```

If it doesn't exist, create a starter file:
```json
{
  "_comment": "Maps short aliases to Jira project keys. Add entries as needed.",
  "projects": []
}
```

Write to `~/.claude/jira-projects.json`.

**Step 3: Optionally discover projects**

Ask: "Would you like me to fetch your Jira project list so you can pick which ones to add?"

If yes:
```bash
source ~/.claude/jira.env
node ~/.claude/skills/jira/scripts/jira-api.js list-projects
```

Show the project list and let the user select which ones to add entries for.
For each selected project, ask for the alias they want to use (default: lowercase project key).

Update `~/.claude/jira-projects.json` with the chosen projects.

**Step 4: Verify**

```bash
source ~/.claude/jira.env
node ~/.claude/skills/jira/scripts/jira-api.js whoami
```

A successful response shows the authenticated user's name and email.
Report: ✅ Jira connected as [name] ([email])

</process>

<success_criteria>
- `~/.claude/jira.env` exists with all three vars
- `~/.claude/jira-projects.json` exists
- `whoami` command returns a valid user
</success_criteria>
