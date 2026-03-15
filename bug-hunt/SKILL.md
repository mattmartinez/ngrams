---
name: bug-hunt
description: "Run adversarial bug hunting on your codebase. Uses 3 isolated subagents (Hunter, Skeptic, Referee) to find and verify real bugs with high fidelity. Invoke when the user asks for a bug hunt, security audit, or adversarial code review."
argument-hint: "[path/to/scan]"
---

# Bug Hunt - Adversarial Bug Finding

Run a 3-agent adversarial bug hunt on your codebase. Each agent runs in isolation via pi's `subagent` tool.

## Target

The scan target is: $ARGUMENTS

If no target was specified, scan the current working directory.

## Execution Steps

Follow these steps in exact order. Each agent runs as a separate subagent to ensure context isolation.

### Step 1: Read the prompt files

Read these prompt files from the skill directory:
- `~/.gsd/agent/skills/bug-hunt/prompts/hunter.md`
- `~/.gsd/agent/skills/bug-hunt/prompts/skeptic.md`
- `~/.gsd/agent/skills/bug-hunt/prompts/referee.md`

### Step 2: Run the Hunter Agent

Use the `subagent` tool in single mode:
- agent: `worker` (or any general-purpose agent available)
- task: Include the hunter prompt text AND the scan target path. The Hunter must use tools (Read, Bash with find/grep) to examine actual code.

Wait for the Hunter to complete and capture its full output.

### Step 2b: Check for findings

If the Hunter reported TOTAL FINDINGS: 0, skip Steps 3-4 and go directly to Step 5 with a clean report. No need to run Skeptic and Referee on zero findings.

### Step 3: Run the Skeptic Agent

Use the `subagent` tool in single mode with a NEW agent invocation:
- agent: `worker`
- task: Include the skeptic prompt text AND the Hunter's structured bug list (BUG-IDs, files, lines, claims, evidence, severity, points). Do NOT include narrative or methodology text — only the structured findings.

The Skeptic must independently read the code to verify each claim.

### Step 4: Run the Referee Agent

Use the `subagent` tool in single mode with a NEW agent invocation:
- agent: `worker`
- task: Include the referee prompt text AND both:
  - The Hunter's full bug report
  - The Skeptic's full challenge report

The Referee must independently read the code to make final judgments.

### Step 5: Present the Final Report

Display the Referee's final verified bug report to the user. The report must use the detailed format for Critical and Medium bugs — each bug gets its own section with **What happens**, **Real-world impact**, and **Risk if unfixed**. Low-severity bugs can use a compact table.

Include:
1. Summary stats
2. Critical bugs (detailed format, each as its own subsection)
3. Medium bugs (detailed format, each as its own subsection)
4. Low bugs (compact table)
5. Low-confidence items flagged for manual review
6. A collapsed section with dismissed bugs (for transparency)

If zero bugs were confirmed, say so clearly — a clean report is a good result.
