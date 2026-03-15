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
   - **Filename:** `YYYY-MM-DD-HHMMSS-<short-descriptor>.md` where the timestamp is the current local time and the descriptor is a short kebab-case summary (e.g., `2026-03-02-231500-questdb-wal-findings.md`, `2026-03-02-231500-auth-refactor-pr.md`).
   - **Location:** Always write to `local/` at the root of where Claude was launched in.

4. **Tell the user** the filename and a one-line summary of what you wrote. Keep it brief.

## Quality Rules

- Be concise. Match the density the format calls for — a Jira ticket is tighter than a findings doc.
- Use the conversation's actual technical details, not generic placeholders.
- Do not pad with boilerplate or filler sections.
- If the directive is ambiguous about scope (e.g., "latest findings" but there were many topics), focus on the most recent/primary thread of discussion.
- Do not include meta-commentary about the skill itself in the output file.
