---
name: mdthis
description: Generate a markdown file from the current conversation context based on a short directive. Use when the user wants to capture discussion, findings, changes, or decisions as a markdown document. Not for writing or editing project documentation (README, docs/, ADRs) or any file the user wants at a specific repo path — mdthis only produces one-off timestamped artifacts in local/, distilled from the conversation.
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
   - **Filename:** `YYYY-MM-DD-HHMMSS-<descriptor>.md`. Get the timestamp from the shell — run `date +%Y-%m-%d-%H%M%S` — never guess it from context. Descriptor = format word plus the topic, kebab-case, ≤6 tokens; take the topic from the directive if it names one, otherwise from the conversation's dominant/most-recent thread (e.g. `2026-03-02-231500-questdb-wal-findings.md`, `2026-03-02-231500-auth-refactor-pr.md`).
   - **Location:** Always write to `local/` at the root of where Claude was launched in (see Prerequisites & Output).

4. **Tell the user** the filename and a one-line summary of what you wrote. Keep it brief.

## Quality Rules

- Be concise. Match the density the format calls for — a Jira ticket is tighter than a findings doc. Paste-destination formats (tickets, PR comments) should be paste-ready: roughly 40 lines or fewer.
- Never copy secret values (credentials, tokens, API keys, connection strings, private keys) from the conversation into the file; replace each with `<REDACTED>` or a reference to where it lives (e.g. `~/.claude/jira.env`). Query results and technical details are fine — redact secret values only.
- Use the conversation's actual technical details, not generic placeholders.
- Do not pad with boilerplate or filler sections.
- If the directive is ambiguous about scope (e.g., "latest findings" but there were many topics), focus on the most recent/primary thread of discussion.
- Do not include meta-commentary about the skill itself in the output file.

## Directive Patterns

- **(empty)** — If the directive is blank, treat it as bare `summary`: apply the scoping rules below, use `summary` as the filename descriptor, and state the chosen scope in the document's intro.
- **Bare format name** (`findings`, `doc`, `runbook`, `ticket`, `notes`) — scope to the conversation's substantive content. If there's a single dominant topic, use it. If the conversation is multi-topic, scope to the last 3-5 exchanges and state the scope choice in the document's intro — e.g. "Scoped to the most recent thread: database migration. Re-run with `findings on auth refactor` for the earlier one."
- **Qualified directive** (format word + topic phrase, e.g. `findings on cache layer`, `PR description for auth refactor`) — scope to the subset of the conversation that discusses the named topic; ignore unrelated tangents even if they were the most recent exchanges.

## Prerequisites & Output

- Output dir = `<launch-root>/local/`. Determine the launch-root from the session context: in Claude Code it is stated verbatim in the `<env>` block as "Working directory" — use that value. If no such env info exists, fall back to the nearest ancestor of the current cwd containing `.git/` and write to `<that>/local/`. Report the resolved absolute path in your reply.
- Create `local/` (`mkdir -p`) if it's missing. On filename collision, append `-2`, `-3`, … to the descriptor until the name is free; never overwrite.
- If the launch-root is a git repo and `git check-ignore -q local` fails, note in your one-line reply that `local/` is not gitignored so the user can decide.
