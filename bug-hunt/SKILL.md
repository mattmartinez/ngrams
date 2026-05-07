---
name: bug-hunt
description: "Run adversarial bug hunting on your codebase. Uses 3 isolated subagents (Hunter, Skeptic, Referee) to find and verify real bugs with high fidelity. Invoke when the user asks for a bug hunt, security audit, or adversarial code review."
argument-hint: "[path/to/scan] [--diff] [--commit sha] [--pr number]"
---

# Bug Hunt - Adversarial Bug Finding

Run a 3-agent adversarial bug hunt on your codebase. Each agent runs in isolation via pi's `subagent` tool.

## Target

The scan target is: $ARGUMENTS

Supported target formats:
- **Directory path:** Scan all source files in the directory
- **File path:** Scan a single file
- **`--diff`:** Scan only files changed vs the default branch
  ```bash
  git diff --name-only $(git merge-base HEAD main)..HEAD
  ```
- **`--commit [sha]`:** Scan only files changed in a specific commit
  ```bash
  git diff --name-only [sha]^..[sha]
  ```
- **`--pr [number]`:** Scan files changed in a GitHub PR (requires gh CLI)
  ```bash
  gh pr diff [number] --name-only
  ```

When in diff mode, the Hunter should focus on the CHANGED lines but read surrounding context to understand the full picture. Pay extra attention to:
- New code that doesn't handle existing edge cases
- Changed interfaces where callers weren't updated
- Removed guards/checks without replacement

If no target was specified, scan the current working directory.

## Execution Steps

Follow these steps in exact order. Each agent runs as a separate subagent to ensure context isolation.

### Step 1: Read the prompt files

Read these prompt files from the skill directory:
- `~/.gsd/agent/skills/bug-hunt/prompts/hunter.md`
- `~/.gsd/agent/skills/bug-hunt/prompts/skeptic.md`
- `~/.gsd/agent/skills/bug-hunt/prompts/referee.md`

### Step 1.1: Load profile (if present)

Search for a `.bug-hunt-profile.md` file by walking up from the scan target directory:

```bash
dir="[target]"
while [ "$dir" != "/" ]; do
  if [ -f "$dir/.bug-hunt-profile.md" ]; then
    echo "Found profile: $dir/.bug-hunt-profile.md"
    break
  fi
  dir="$(dirname "$dir")"
done
```

If found, read the profile file. The profile contains:
- **Severity overrides** — domain-specific escalations (e.g., "hardcoded secrets → Critical" in financial services)
- **Domain-specific checks** — additional checklist items the generic prompt doesn't cover

**How to apply the profile:**
- Prepend the profile's severity overrides to the Hunter's task (BEFORE the generic severity calibration — profile overrides take precedence)
- Prepend the profile's severity overrides to the Referee's task as well
- Append the profile's domain-specific checks to the Hunter's task (AFTER the generic checklist)
- If no profile is found, proceed with the generic prompts only

### Step 1.5: Scope assessment and stack detection

**Resolve the target file list:**

If the target uses `--diff`, `--commit`, or `--pr`, run the appropriate git/gh command to get the list of changed files. Pass this explicit file list to the Hunter instead of a directory.

**Count source files in the target:**

```bash
find [target] -type f \( -name "*.ts" -o -name "*.js" -o -name "*.py" \
  -o -name "*.go" -o -name "*.rs" -o -name "*.rb" -o -name "*.java" \
  -o -name "*.swift" -o -name "*.kt" -o -name "*.c" -o -name "*.cpp" \
  -o -name "*.h" \) | grep -v node_modules | grep -v vendor | \
  grep -v dist | grep -v __pycache__ | grep -v '.test.' | \
  grep -v '.spec.' | wc -l
```

- **If > 50 source files:** Run multiple Hunter agents in parallel using `subagent` parallel mode, each assigned a different directory/module. Merge their findings before passing to the Skeptic.
- **If > 150 source files:** Additionally prioritize — run a quick recon agent first to identify the highest-risk modules (auth, data access, API handlers, config), then assign Hunters only to those modules.

**Detect the project stack:**

```bash
ls [target]/package.json [target]/tsconfig.json [target]/Cargo.toml \
   [target]/go.mod [target]/pyproject.toml [target]/requirements.txt \
   [target]/Gemfile [target]/pom.xml [target]/Package.swift 2>/dev/null
```

Append the relevant framework-specific checklist to the Hunter's task (see the language-specific sections in `hunter.md`). Include all that apply — a project can use multiple languages.

### Step 2: Run the Hunter Agent

Use the `subagent` tool in single mode (or parallel mode for large codebases):
- agent: `worker` (or any general-purpose agent available)
- task: Include the hunter prompt text AND the scan target path AND the detected stack-specific checklist. The Hunter must use tools (Read, Bash with find/grep) to examine actual code.

Wait for the Hunter to complete and capture its full output.

Extract the content between `===HUNTER_FINDINGS_START===` and `===HUNTER_FINDINGS_END===` delimiters. This is the structured findings payload.

### Step 2b: Check for findings

If the Hunter reported TOTAL FINDINGS: 0, skip Steps 3-4 and go directly to Step 5 with a clean report. No need to run Skeptic and Referee on zero findings.

### Step 3: Run the Skeptic Agent

Use the `subagent` tool in single mode with a NEW agent invocation:
- agent: `worker`
- task: Include the skeptic prompt text AND the Hunter's structured findings (content between the `===HUNTER_FINDINGS_START===` and `===HUNTER_FINDINGS_END===` delimiters — BUG-IDs, files, lines, claims, evidence, severity, points). Do NOT include narrative or methodology text — only the structured findings.

The Skeptic must independently read the code to verify each claim.

Extract the content between `===SKEPTIC_REPORT_START===` and `===SKEPTIC_REPORT_END===` delimiters.

### Step 4: Run the Referee Agent

Use the `subagent` tool in single mode with a NEW agent invocation:
- agent: `worker`
- task: Include the referee prompt text AND both:
  - The Hunter's full findings (between `===HUNTER_FINDINGS_START===` / `===HUNTER_FINDINGS_END===`)
  - The Skeptic's full report (between `===SKEPTIC_REPORT_START===` / `===SKEPTIC_REPORT_END===`)

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
