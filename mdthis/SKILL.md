---
name: mdthis
description: Generate a markdown file from the current conversation context based on a short directive. Use when the user wants to capture discussion, findings, changes, or decisions as a markdown document.
argument-hint: <what to write, e.g. "jira ticket", "PR comment", "latest findings">
---

# mdthis — Conversation-to-Markdown Skill

You have full access to the current conversation context. The user wants you to produce a markdown file based on everything discussed so far, shaped by their directive.

## Directive

$ARGUMENTS

## Instructions

1. **Infer the format from the directive.** Match the tone, structure, and conventions of the implied document type:
   - "jira ticket" / "ticket" → Title, Description, Acceptance Criteria, Technical Notes
   - "PR comment" / "PR description" → Summary, Changes, Test Plan
   - "latest findings" / "findings" → structured summary of discoveries, conclusions, open questions
   - "outline" / "summary" → hierarchical overview of what was discussed
   - "runbook" / "playbook" → step-by-step operational instructions
   - "bug report" → Steps to Reproduce, Expected vs Actual, Root Cause, Fix
   - "meeting notes" / "status update" → agenda items, decisions, action items
   - "doc" / "documentation" → technical documentation for the topic discussed
   - Anything else → use your best judgment to match the intent

2. **Pull content from the conversation.** Do NOT ask the user to re-explain. Everything you need is in the conversation history. Focus on:
   - Key decisions and their rationale
   - Technical details, code changes, file paths
   - Problems identified and solutions applied
   - Open questions or next steps

3. **Write the file.** Use the Write tool to save the markdown file.
   - **Filename:** `YYYY-MM-DD-HHMMSS-<short-descriptor>.md`. Get the timestamp from the shell — run `date +%Y-%m-%d-%H%M%S` — never guess it from context. The descriptor is a short kebab-case summary (e.g., `2026-03-02-231500-questdb-wal-findings.md`, `2026-03-02-231500-auth-refactor-pr.md`).
   - **Location:** Always write to `local/` at the root of where Claude was launched in.

4. **Tell the user** the filename and a one-line summary of what you wrote. Keep it brief.

## Quality Rules

- Be concise. Match the density the format calls for — a Jira ticket is tighter than a findings doc.
- Use the conversation's actual technical details, not generic placeholders.
- Do not pad with boilerplate or filler sections.
- If the directive is ambiguous about scope (e.g., "latest findings" but there were many topics), focus on the most recent/primary thread of discussion.
- Do not include meta-commentary about the skill itself in the output file.

## Filename Inference

Filenames follow the template `{date}-{time}-{descriptor}.md`, where `{date}` is `YYYY-MM-DD`, `{time}` is `HHMMSS` in local time, and `{descriptor}` is derived from the directive.

### Descriptor algorithm

Apply these steps to the directive in order:

1. **Lowercase** the directive.
2. **Drop stop-words** anywhere in the phrase: `the`, `a`, `an`, `on`, `for`, `of`, `in`, `to`, `from`.
3. **Drop format-noise words** that restate the format type without adding meaning: `description`, `latest`, `recent`, `current`, `quick`, `short`, `brief`. Keep them only when they are the *only* signal in a bare directive (e.g. `latest` alone — fall back to a sensible default like `findings`).
4. **Replace** any remaining whitespace and punctuation with single hyphens.
5. **Collapse** runs of hyphens, then trim leading/trailing hyphens.
6. **Cap at ~6 hyphenated tokens.** If the descriptor exceeds 6 tokens, drop the trailing tokens until it fits, preserving the format word and the most specific topic words near the front.

### Worked examples

| Input directive | Descriptor | Full filename |
|-----------------|------------|---------------|
| `jira ticket` | `jira-ticket` | `2026-05-07-103015-jira-ticket.md` |
| `PR description for auth refactor` | `pr-auth-refactor` | `2026-05-07-103015-pr-auth-refactor.md` |
| `latest findings on cache layer` | `findings-cache-layer` | `2026-05-07-103015-findings-cache-layer.md` |
| `bug report for the websocket reconnect loop` | `bug-report-websocket-reconnect-loop` | `2026-05-07-103015-bug-report-websocket-reconnect-loop.md` |
| `runbook for on-call rotation handoff` | `runbook-on-call-rotation-handoff` | `2026-05-07-103015-runbook-on-call-rotation-handoff.md` |
| `meeting notes from the platform sync about quarterly priorities and staffing` | `meeting-notes-platform-sync-quarterly-priorities` | `2026-05-07-103015-meeting-notes-platform-sync-quarterly-priorities.md` |

The last row demonstrates the 6-token cap: trailing tokens (`and`, `staffing`) are dropped after stop-word removal once the descriptor would otherwise exceed the limit.

## Directive Patterns

The `$ARGUMENTS` directive can take three grammatical forms. Recognizing the form determines how much of the conversation to scope and how much disambiguation the output must do.

### (a) Bare format name

A single format word with no topic phrase: `findings`, `doc`, `runbook`, `ticket`, `notes`. Scope = the substantive content of the conversation. If the conversation has a single dominant topic, focus on that topic implicitly. If the conversation is multi-topic, fall back to the **last 3-5 exchanges** (see (c) below) and call out the scope choice in the document's intro paragraph.

### (b) Qualified directive

Format word + topic phrase, typically introduced with `for`, `on`, `about`, or `from`: `findings on cache layer`, `PR description for auth refactor`, `bug report for the websocket reconnect loop`. Scope = the subset of the conversation that discusses the named topic. Ignore unrelated tangents even if they were the most recent exchanges.

### (c) Ambiguous directive (multi-topic conversation)

When the directive is bare *and* the conversation covered multiple unrelated topics, default to the **last 3-5 exchanges** as the scope boundary. State this scope choice explicitly in the document's intro — for example: "Captured from the most recent thread of discussion: <topic>." This gives the user a clear signal that earlier topics were not included and lets them re-run with a qualified directive if they wanted something else.

### Examples

| # | Directive | Form | Parsed Intent | Scope Boundary | Output Focus |
|---|-----------|------|---------------|----------------|--------------|
| 1 | `findings` | (a) bare | general findings doc | full conversation if single-topic; last 3-5 exchanges if multi-topic | structured summary of discoveries, conclusions, open questions |
| 2 | `findings on cache layer` | (b) qualified | findings limited to cache layer | turns mentioning cache layer (filter out other topics) | cache-layer findings only |
| 3 | `jira ticket` | (a) bare | issue ticket for primary task | most recent actionable problem in the conversation | title, description, acceptance criteria, technical notes |
| 4 | `jira ticket for the migration bug` | (b) qualified | ticket for a specific bug | exchanges about the migration bug | reproduction steps + acceptance criteria + fix scope |
| 5 | `PR description` | (a) bare | PR write-up for most recent code work | last 3-5 exchanges if multi-topic; the active code change otherwise | summary, changes, test plan |
| 6 | `PR description for auth refactor` | (b) qualified | PR write-up for the auth refactor | auth-refactor exchanges only | summary, changes, test plan, scoped to auth refactor |
| 7 | `runbook` | (a) bare | step-by-step procedure | the primary operational topic | numbered ops steps with prereqs and rollback |
| 8 | `runbook for the on-call rotation handoff` | (b) qualified | handoff runbook | rotation-handoff exchanges | step-by-step handoff procedure |
| 9 | `meeting notes` | (a) bare | decisions + action items | full conversation | agenda items, decisions, owners, action items |
| 10 | `bug report` | (a) bare ambiguous | bug write-up | last 3-5 exchanges; scope called out in intro | reproduction, expected vs actual, root cause, fix |

### Ambiguity resolution in practice

If the conversation discussed an auth refactor *and* a database migration, then `findings` is ambiguous. The output should:

1. Scope to whichever topic dominates the last 3-5 exchanges.
2. Open with an intro line such as: "Scoped to the most recent thread: database migration. The earlier auth-refactor discussion is not included — re-run with `findings on auth refactor` to capture that."

This makes the scope choice auditable and cheap to correct.

## Prerequisites & Errors

This section pins down where files land and what to do when filesystem state is unexpected. Apply these rules every time, without prompting the user.

### Output location: always `<launch-root>/local/`

- **Launch-root** is the working directory where the agent (Claude Code, the harness, etc.) was *originally launched*. It is **not** the agent's current working directory at the moment the skill runs — those can diverge if the agent ran `cd` into a subdirectory or was invoked from a nested shell.
- All output goes to `<launch-root>/local/`. Never write to `./local/` blindly, never write to the current working directory's `local/`, and never write outside `local/`.
- If you cannot determine the launch-root with certainty, fall back to the closest ancestor directory of the current cwd that looks like a project root (contains `.git/`, `package.json`, `pyproject.toml`, etc.) and write to `<that>/local/`. State the resolved path in your reply to the user so they can correct it if wrong.

### Create `local/` if missing

- If `<launch-root>/local/` does not exist, create it (recursively, mode 0755) before writing the file. Do not prompt the user — directory creation is part of the skill's contract.
- Do not create any other directories. The skill writes flat into `local/`; it does not nest by date, topic, or directive.

### Filename collisions: append a numeric suffix

- Before writing, check whether the target path already exists.
- If it does, append `-2` to the descriptor (before `.md`) and re-check. If `-2` also exists, try `-3`, then `-4`, and so on until you find a free name.
- Never overwrite an existing file. The timestamp portion of the filename usually prevents collisions, but two invocations within the same second (or a re-run with the same directive at the same minute granularity, depending on how the timestamp is rendered) will collide — the suffix rule handles that.

Example collision walk:

```
2026-05-07-103015-pr-auth-refactor.md       ← exists
2026-05-07-103015-pr-auth-refactor-2.md     ← exists
2026-05-07-103015-pr-auth-refactor-3.md     ← write here
```

### Worked example: path resolution from a subdirectory

Agent was launched in `/Users/kim/project`, then `cd`'d into `/Users/kim/project/sub` before the user invoked `/mdthis findings`. The skill must:

1. Identify launch-root as `/Users/kim/project` (not `/Users/kim/project/sub`).
2. Resolve the output directory to `/Users/kim/project/local/`.
3. Create `/Users/kim/project/local/` if it does not already exist.
4. Compute the filename, e.g. `2026-05-07-103015-findings.md`.
5. Check `/Users/kim/project/local/2026-05-07-103015-findings.md` for existence; suffix with `-2`, `-3`, ... if needed.
6. Write to the resolved absolute path — **not** to `/Users/kim/project/sub/local/...`.

The output reported to the user should always reference the absolute path, so the user can verify the file landed where they expect even when their shell is in a different directory.
