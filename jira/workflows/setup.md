<objective>
Walk through one-time credential setup so the Jira skill works from every terminal.
</objective>

<process>

**Step 1: Collect credentials**

Use `secure_env_collect` to prompt for the three required values:

```
keys:
  - key: JIRA_BASE_URL
    hint: "https://yourorg.atlassian.net"
    guidance:
      - "Go to your Jira instance in a browser"
      - "Copy the base URL — everything up to but not including /browse or /jira"
      - "Example: https://acme.atlassian.net"

  - key: JIRA_EMAIL
    hint: "you@example.com"
    guidance:
      - "The email address you use to log into Jira"

  - key: JIRA_API_TOKEN
    hint: "starts with ATATT3x..."
    guidance:
      - "Go to: https://id.atlassian.com/manage-profile/security/api-tokens"
      - "Click 'Create API token'"
      - "Give it a name like 'agent-cli' and copy the token"
    required: true
```

Write to destination: `dotenv`, envFilePath: `~/.gsd/jira.env`

**Step 2: Create project map**

Check if `~/.gsd/jira-projects.json` already exists:
```bash
cat ~/.gsd/jira-projects.json 2>/dev/null
```

If it doesn't exist, create a starter file:
```json
{
  "_comment": "Maps short aliases to Jira project keys. Add entries as needed.",
  "projects": []
}
```

Write to `~/.gsd/jira-projects.json`.

**Step 3: Optionally discover projects**

Ask: "Would you like me to fetch your Jira project list so you can pick which ones to add?"

If yes:
```bash
source ~/.gsd/jira.env
node ~/.gsd/agent/skills/jira/scripts/jira-api.js list-projects
```

Show the project list and let the user select which ones to add entries for.
For each selected project, ask for the alias they want to use (default: lowercase project key).

Update `~/.gsd/jira-projects.json` with the chosen projects.

**Step 4: Verify**

```bash
source ~/.gsd/jira.env
node ~/.gsd/agent/skills/jira/scripts/jira-api.js whoami
```

A successful response shows the authenticated user's name and email.
Report: ✅ Jira connected as [name] ([email])

</process>

<success_criteria>
- `~/.gsd/jira.env` exists with all three vars
- `~/.gsd/jira-projects.json` exists
- `whoami` command returns a valid user
</success_criteria>
