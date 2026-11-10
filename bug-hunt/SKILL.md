---
name: bug-hunt
description: "Run adversarial bug hunting on your codebase. Uses 3 isolated subagents (Hunter, Skeptic, Referee) to find and verify real bugs with high fidelity. Invoke when the user asks for a bug hunt, security audit (line-level bugs and vulnerabilities), or adversarial code review — for attack-path or threat-model assessment use red-team."
argument-hint: "[path/to/scan] [--diff] [--commit sha] [--pr number]"
---

# Bug Hunt - Adversarial Bug Finding

Run a 3-agent adversarial bug hunt on your codebase. Each agent runs as an isolated Agent tool subagent.

## Target

The scan target is: $ARGUMENTS

Supported target formats:
- **Directory path:** Scan all source files in the directory
- **File path:** Scan a single file
- **`--diff`:** Scan only files changed vs the default branch (from the merge-base to HEAD)
- **`--commit [sha]`:** Scan only files changed in a specific commit
- **`--pr [number]`:** Scan files changed in a GitHub PR (requires gh CLI)

When in diff mode, the Hunter should focus on the CHANGED lines but read surrounding context to understand the full picture. Pay extra attention to:
- New code that doesn't handle existing edge cases
- Changed interfaces where callers weren't updated
- Removed guards/checks without replacement

If no target was specified, scan the current working directory.

## Execution Steps

Follow these steps in exact order. Each agent runs as a separate subagent to ensure context isolation.

**Marker rule (applies to the Hunter, Skeptic and Referee).** Each agent must wrap its result in its start and end markers. If a reply lacks either marker, run that stage once more with the same input. If the second reply also lacks them, stop and tell the user which stage failed. With parallel Hunters, re-run only the Hunter that failed.

### Step 1: Read the prompt files

Read these files using the skill directory variable:
- `${CLAUDE_SKILL_DIR}/prompts/hunter.md`
- `${CLAUDE_SKILL_DIR}/prompts/skeptic.md`
- `${CLAUDE_SKILL_DIR}/prompts/referee.md`

### Step 1.1: Resolve the target

Run this script. Run it, do not read it.

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/resolve_target.py --profile-name .bug-hunt-profile.md --ext ts,js,py,go,rs,rb,java,swift,kt,c,cpp,h -- <target>
```

`<target>` is the scan target given above, in one of the supported formats: a path (quote it if it has spaces), `--diff`, `--commit <sha>`, `--pr <number>`, or nothing at all for the current directory. If the user described the target in words instead of giving a path, find the folder or file they mean first and pass that path.

It prints one JSON object on stdout and logs its steps to stderr. It never changes the working tree. The fields this skill uses:
- `mode`: `path`, `diff`, `commit` or `pr`.
- `target`: the absolute path scanned in path mode; null in the diff modes.
- `repo_root`: the git repository root, or null.
- `file_count`: how many source files are in scope, after skipping tests, specs and `node_modules`, `vendor`, `dist`, `__pycache__` and `.git`.
- `files_list`: a text file with one in-scope file per line.
- `diff_file`: in the three diff modes, the saved unified diff; null in path mode.
- `checked_out`: whether the files under review match what is checked out. HEAD may differ from the commit under review if those files are unchanged since it. `head` is the current commit, `expected_head` the commit under review. Null in path mode.
- `profile`: the absolute path of the nearest `.bug-hunt-profile.md` above the target, or null.
- `notes`: plain-language warnings, such as how many changed files the filters skipped. Pass them on to the user.
- `base`, `commit`, `pr`: the base branch, commit or PR number, when relevant.

**If the script exits with code 2:** show its error message to the user and stop.

**If `checked_out` is false:** stop before launching any agent. Tell the user in one or two plain sentences what is checked out and what is under review. Give the command that fixes it: `gh pr checkout <n>` for a PR; for a commit, check that commit out, or re-run on the current code. Never change the working tree yourself.

### Step 1.2: Load profile (if present)

If `profile` is set, read that file. The profile contains:
- **Severity overrides** — domain-specific escalations (e.g., "hardcoded secrets → Critical" in financial services)
- **Domain-specific checks** — additional checklist items the generic prompt doesn't cover

**How to apply the profile:**
- Prepend the profile's severity overrides to the Hunter's task (BEFORE the generic severity calibration — profile overrides take precedence)
- Prepend the profile's severity overrides to the Referee's task as well
- Append the profile's domain-specific checks to the Hunter's task (AFTER the generic checklist)
- If no profile is found, proceed with the generic prompts only

### Step 1.5: Scope assessment and stack detection

**Resolve the target file list:**

If the target uses `--diff`, `--commit`, or `--pr`, pass the explicit file list from `files_list` to the Hunter instead of a directory. Also pass the path in `diff_file`, and tell the Hunter to read the diff first, then the surrounding code.

**If `file_count` is 0:** Report 0 findings immediately and skip directly to Step 5. No agents need to run — there is nothing to analyze.
_Rationale:_ A `--diff` with no changes, an empty directory, or a filter that excludes everything is not a bug-free codebase, just a no-op scan. Spending agent budget on zero files is waste, and running Hunter against nothing produces noise rather than signal.

**Use `file_count` to size the hunt:**

- **If > 50 source files:** Run multiple Hunter agents in parallel by issuing multiple Agent calls in a single message, each assigned a different directory/module; merge findings before the Skeptic.
- **If > 150 source files:** Run a recon agent first to identify the highest-risk modules (auth, data access, API handlers, config, payment/permissions, parsing), then dispatch parallel Hunters scoped to those modules only — do not scan the whole tree.
  _Rationale:_ Token and time budget for a single Hunter run scales with file count. Beyond ~150 files the recon-then-targeted-hunters pattern produces higher-fidelity findings than a single broad scan, and avoids context exhaustion on the Hunter.

**Detect the project stack:**

```bash
ls <dir>/package.json <dir>/tsconfig.json <dir>/Cargo.toml \
   <dir>/go.mod <dir>/pyproject.toml <dir>/requirements.txt \
   <dir>/Gemfile <dir>/pom.xml <dir>/Package.swift 2>/dev/null
```

`<dir>` is `target` in path mode (for a single file, its folder), and `repo_root` in the diff modes.

Append the relevant framework-specific checklist to the Hunter's task (see the language-specific sections in `hunter.md`). Include all that apply — a project can use multiple languages.

### Step 2: Run the Hunter Agent

Launch a new Agent tool subagent (subagent_type: general-purpose):
- task: Include the hunter prompt text AND the scan target path AND the detected stack-specific checklist. In the diff modes, also include the `diff_file` path with the instruction to read the diff first and then the surrounding code. The Hunter must use tools (Read, Bash with find/grep) to examine actual code.

**Parallel-hunter BUG-ID namespacing:** When running multiple Hunters in parallel (per the Step 1.5 thresholds), assign each Hunter a distinct BUG-ID prefix so findings remain unique across the merge. The assignment scheme is:

- Hunter-A → reports `BUG-101`, `BUG-102`, `BUG-103`, … (BUG-1xx)
- Hunter-B → reports `BUG-201`, `BUG-202`, `BUG-203`, … (BUG-2xx)
- Hunter-C → reports `BUG-301`, `BUG-302`, `BUG-303`, … (BUG-3xx)

Append the assigned prefix to each Hunter's task instructions (e.g. "You are Hunter-A. Prefix all your findings with BUG-1xx."). When merging the parallel findings, preserve each Hunter's namespace verbatim — do NOT renumber findings into a single contiguous sequence. The Skeptic receives the merged findings with all namespaces preserved so it can attribute claims back to the originating Hunter and so cross-Hunter duplicate claims become visible by their distinct BUG-IDs.

In single-Hunter mode, no prefix is assigned and the Hunter uses its default `BUG-1`, `BUG-2`, … numbering.

Wait for the Hunter to complete and capture its full output.

Extract the content between `===HUNTER_FINDINGS_START===` and `===HUNTER_FINDINGS_END===` delimiters. This is the structured findings payload. If either marker is missing, apply the marker rule.

### Step 2b: Check for findings

If the Hunter reported TOTAL FINDINGS: 0, skip Steps 3-4 and go directly to Step 5 with a clean report. No need to run Skeptic and Referee on zero findings.

### Step 3: Run the Skeptic Agent

Launch a new Agent tool subagent (subagent_type: general-purpose):
- task: Include the skeptic prompt text AND the Hunter's structured findings (content between the `===HUNTER_FINDINGS_START===` and `===HUNTER_FINDINGS_END===` delimiters — BUG-IDs, files, lines, claims, evidence, severity). Do NOT include narrative or methodology text — only the structured findings.

The Skeptic must independently read the code to verify each claim.

Extract the content between `===SKEPTIC_REPORT_START===` and `===SKEPTIC_REPORT_END===` delimiters. If either marker is missing, apply the marker rule.

### Step 4: Run the Referee Agent

Launch a new Agent tool subagent (subagent_type: general-purpose):
- task: Include the referee prompt text AND both:
  - The Hunter's full findings (between `===HUNTER_FINDINGS_START===` / `===HUNTER_FINDINGS_END===`)
  - The Skeptic's full report (between `===SKEPTIC_REPORT_START===` / `===SKEPTIC_REPORT_END===`)

The Referee must independently read the code to make final judgments.

Extract the content between `===REFEREE_REPORT_START===` and `===REFEREE_REPORT_END===` delimiters. If either marker is missing, apply the marker rule.

### Step 5: Give the short answer first

If you skipped to this step because no files were in scope or the Hunter found nothing, there is no Referee report: say so in one line and stop.

Otherwise take only the text between `===REFEREE_REPORT_START===` and `===REFEREE_REPORT_END===`. Keep it in context; do not print it.

Reply in chat with:
1. One line of counts: reported, dismissed, confirmed (Critical / Medium / Low).
2. A numbered list of the confirmed bugs, worst first, one line each, at most 10:
   `1. path/file.py:42 · what goes wrong, in everyday words · Critical`
3. If there are more than 10, one more line per severity that groups the rest ("+ 6 more Low: 4 unchecked inputs, 2 leaked file handles").
4. One line naming any low-confidence items that need a manual look.
5. One closing sentence: "Say 'look at 2' for the detail and a fix, or 'full report' for everything."

Write no file unless asked. If zero bugs were confirmed, say so plainly — a clean report is a good result — and offer the dismissed list.

**"Look at N."** Print a card for that bug from the Referee's entry: **What happens**, **Fix** (with its complexity and risks), **Risk if left**, **How to check it worked** (a test to add or a command to run; say when this is your suggestion and not the Referee's), then the evidence (file, lines, category, test coverage). Plain words, short sentences.

**"Full report."** Print the Referee's report as written.

Do not change the format the Referee is asked to produce: another tool parses it.
