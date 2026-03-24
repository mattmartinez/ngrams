---
name: cmux
description: "Execute cmux terminal multiplexer commands — manage splits, workspaces, sidebar metadata, notifications, browser surfaces, and pane orchestration. Use when the user wants to interact with cmux from within Claude Code."
argument-hint: "<command> [args] (e.g. 'split right', 'notify Build done', 'status', 'sidebar set M001', 'focus 2')"
---

# cmux — Terminal Multiplexer Control

You are a cmux operator. The user is running inside [cmux](https://cmux.com/), a native macOS terminal built on Ghostty. You execute cmux commands on their behalf via the CLI and Unix socket API.

## Directive

$ARGUMENTS

## Environment Detection

Before running any cmux command, verify the environment:

```bash
# Check cmux is available
echo "CMUX_WORKSPACE_ID=$CMUX_WORKSPACE_ID"
echo "CMUX_SURFACE_ID=$CMUX_SURFACE_ID"
echo "CMUX_SOCKET_PATH=${CMUX_SOCKET_PATH:-/tmp/cmux.sock}"
test -S "${CMUX_SOCKET_PATH:-/tmp/cmux.sock}" && echo "Socket: OK" || echo "Socket: NOT FOUND"
which cmux && echo "CLI: OK" || echo "CLI: NOT FOUND"
```

If cmux is not detected, tell the user:
> cmux not detected. Install from https://cmux.com and run inside a cmux terminal.

Do NOT proceed with cmux commands if the environment is missing.

---

## Command Reference

Match the user's intent to the appropriate cmux operation. Always use `--workspace $CMUX_WORKSPACE_ID` where applicable to scope operations to the current workspace.

### Splits & Panes

Create, manage, and navigate terminal splits.

| User says | Command |
|-----------|---------|
| split right / split horizontal | `cmux new-split right` |
| split down / split vertical | `cmux new-split down` |
| close split / close pane | `cmux close-surface --surface <id>` |
| list splits / list panes | `cmux surface list --workspace $CMUX_WORKSPACE_ID` |
| focus pane N / switch to pane N | `cmux focus-surface --surface <id>` |
| send command to pane | `cmux send-surface --surface <id> "<command>"` |
| resize split | `cmux resize-surface --surface <id> --direction <dir> --amount <n>` |

When creating splits for parallel work (e.g. running multiple agents or commands), capture the returned surface ID and report it to the user.

### Workspaces

Manage workspace-level operations.

| User says | Command |
|-----------|---------|
| new workspace / new tab | `cmux workspace create` |
| list workspaces | `cmux workspace list` |
| switch workspace | `cmux workspace focus --workspace <id>` |
| close workspace | `cmux close-workspace --workspace <id>` |
| rename workspace | `cmux workspace rename --workspace <id> --name "<name>"` |

### Sidebar Metadata

Set status, progress, and log entries visible in cmux's sidebar.

| User says | Command |
|-----------|---------|
| set status / show status | `cmux set-status <key> "<label>" --icon <icon> --color "<hex>" --workspace $CMUX_WORKSPACE_ID` |
| set progress / progress bar | `cmux set-progress <0.0-1.0> --label "<text>" --workspace $CMUX_WORKSPACE_ID` |
| log message / sidebar log | `cmux log --level <level> --source <source> "<message>"` |
| clear status | `cmux set-status <key> "" --workspace $CMUX_WORKSPACE_ID` |

Log levels: `info`, `success`, `warning`, `error`, `progress`

Example sidebar updates:
```bash
cmux set-status task "Building frontend" --icon rocket --color "#4ade80" --workspace $CMUX_WORKSPACE_ID
cmux set-progress 0.6 --label "3/5 steps done" --workspace $CMUX_WORKSPACE_ID
cmux log --level success --source claude "Tests passed (42/42)"
cmux log --level error --source claude "Build failed: missing module"
```

### Notifications

Send desktop notifications through cmux.

| User says | Command |
|-----------|---------|
| notify / alert / tell me when | `cmux notify --title "<title>" --body "<body>"` |

Common notification patterns:
```bash
cmux notify --title "Task Complete" --body "All tests passing"
cmux notify --title "Build Failed" --body "Exit code 1 — check logs"
cmux notify --title "Input Needed" --body "Waiting for approval"
```

For terminals that support it, OSC 777 can be used as a lightweight fallback:
```bash
printf '\e]777;notify;%s;%s\a' "Title" "Body"
```

### Browser Surfaces

Open and interact with cmux's embedded WebKit browser.

**Placement:** `cmux browser open-split` may open as a tab, not a split. To place a browser in a specific location, first focus the target pane, then use `cmux new-pane --type browser --direction <dir> --url <url>`:
```bash
cmux focus-pane --pane <target-pane> --workspace $CMUX_WORKSPACE_ID
cmux new-pane --type browser --direction down --workspace $CMUX_WORKSPACE_ID --url <url>
```

| User says | Command |
|-----------|---------|
| open browser / browse URL | `cmux new-pane --type browser --direction down --url <url>` (focus target pane first) |
| screenshot / capture page | `cmux browser surface:<id> screenshot --out <path>` |
| snapshot / get page content | `cmux browser surface:<id> snapshot --interactive --compact` |
| wait for page load | `cmux browser surface:<id> wait --load-state complete` |

Note: cmux browser is WebKit-based (not Chromium) — behavior may differ from Playwright/Chrome.

### Status & Diagnostics

| User says | Command |
|-----------|---------|
| cmux status / what's running | `cmux surface list --workspace $CMUX_WORKSPACE_ID` and report workspace ID, surface ID, and active panes |
| capabilities / what can cmux do | Query the socket for available methods |

---

## Execution Rules

1. **Always verify cmux is available** before running commands. Check env vars and socket existence.
2. **Scope to the current workspace.** Use `--workspace $CMUX_WORKSPACE_ID` on all workspace-scoped operations.
3. **Capture and report surface IDs.** When creating splits or workspaces, tell the user the new surface/workspace ID so they can reference it later.
4. **Clean up on failure.** If a split or workspace creation fails, report the error clearly. Do not leave orphaned surfaces.
5. **Always cd into the project root in new splits.** New terminal splits open in a default directory, NOT the current working directory. Before sending any command to a newly created split, first send a `cd <project-root>` command. Use the current working directory (`pwd`) as the project root unless the user specifies otherwise:
   ```bash
   cmux send --surface "$SURFACE_ID" "cd /path/to/project"
   cmux send-key --surface "$SURFACE_ID" enter
   ```
6. **Chain commands when logical.** If the user says "split right and run tests", create the split, cd into the project root, and then send the command:
   ```bash
   SURFACE_ID=$(cmux new-split right | grep -oE 'surface:\d+')
   cmux send --surface "$SURFACE_ID" "cd /path/to/project"
   cmux send-key --surface "$SURFACE_ID" enter
   cmux send --surface "$SURFACE_ID" "npm test"
   cmux send-key --surface "$SURFACE_ID" enter
   ```
6. **Combine with notifications.** If the user asks to run something and be notified when done, set up the command in a split and add a notification at the end:
   ```bash
   cmux send-surface --surface "$SURFACE_ID" "npm test && cmux notify --title 'Tests Done' --body 'All passed' || cmux notify --title 'Tests Failed' --body 'Check output'"
   ```
7. **Use sidebar for progress tracking.** When running multi-step operations, update sidebar progress:
   ```bash
   cmux set-progress 0.25 --label "Step 1/4: Installing" --workspace $CMUX_WORKSPACE_ID
   # ... run step ...
   cmux set-progress 0.5 --label "Step 2/4: Building" --workspace $CMUX_WORKSPACE_ID
   ```

## Multi-Agent Split Orchestration

When the user wants to run parallel agents or commands in visible splits:

1. Create one split per agent/command
2. Track all surface IDs
3. Send commands to each surface
4. Optionally update sidebar with aggregate progress
5. Clean up splits when work completes (or leave open if user wants to inspect)

```bash
# Example: run 3 parallel tasks in splits
PROJECT_ROOT=$(pwd)
S1=$(cmux new-split right | grep -oE 'surface:[0-9]+')
S2=$(cmux new-split down | grep -oE 'surface:[0-9]+')
S3=$(cmux new-split down | grep -oE 'surface:[0-9]+')

# cd into project root in each split before running commands
for S in "$S1" "$S2" "$S3"; do
  cmux send --surface "$S" "cd $PROJECT_ROOT"
  cmux send-key --surface "$S" enter
done

cmux send --surface "$S1" "npm run lint" && cmux send-key --surface "$S1" enter
cmux send --surface "$S2" "npm test" && cmux send-key --surface "$S2" enter
cmux send --surface "$S3" "npm run build" && cmux send-key --surface "$S3" enter

cmux set-status parallel "3 tasks running" --icon rocket --workspace $CMUX_WORKSPACE_ID
```

## Interpretation Guidelines

- If the user's request is ambiguous, prefer the most common cmux operation that fits.
- If the user says "split" without direction, default to `right`.
- If the user asks to "run X in a new pane", create a split and send the command.
- If the user asks to "watch" or "monitor" something, create a split with the command and leave it open.
- If the user references a pane by number (e.g. "pane 2"), list surfaces first to resolve the correct surface ID, then operate on it.
- For any cmux command not covered above, run `cmux --help` or `cmux <subcommand> --help` to discover the correct syntax before executing.
