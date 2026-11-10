---
name: red-team
description: "Run an adversarial red-team assessment on your own codebase. Uses 3 isolated subagents (Attacker, Blue Team, Arbiter) to enumerate attack surface, construct realistic attack paths, and verify which are actually exploitable. Invoke when the user asks for a red-team, threat model, attack-surface review, or adversarial security assessment of a project they own or are authorized to test — for line-level bug or vulnerability hunting use bug-hunt."
argument-hint: "[path/to/assess] [--diff] [--commit sha] [--pr number]"
---

# Red Team — Adversarial Attack-Path Assessment

Run a 3-agent adversarial red-team assessment on a codebase **you own or are explicitly authorized to test**. Each agent runs as an isolated subagent (in Claude Code, the Agent tool with a general-purpose agent).

Where a bug hunt asks *"is this code correct?"*, a red team asks *"how would an adversary abuse this to reach something they shouldn't?"* The unit of work is not a defect on a line — it is an **attack path**: a precondition, a sequence of steps a chosen attacker persona can actually perform, and the asset it compromises. Isolated weaknesses matter only insofar as they form, or chain into, a reachable path to an asset.

## Rules of engagement (read first)

This skill performs a **paper assessment of source, config, and infrastructure-as-code in the repository**. It reasons about exploitability from the code; it does **not** launch live attacks, send traffic to running systems, run exploit payloads, exfiltrate real data, or modify any deployed environment.

- Only assess targets the user owns or is authorized to test. If the target looks like it belongs to a third party (vendored SDK, upstream dependency, someone else's service), confirm scope before proceeding.
- Agents may read code and run **read-only** local recon (`find`, `grep`, reading manifests/configs). They must not execute the project's binaries against real infrastructure, hit network endpoints, or run anything destructive.
- Output is a findings report with reproduction *reasoning* and remediation — not weaponized exploit code. Proof-of-concept sketches should be the minimum needed to establish that a path is real.

## Target

The assessment target is: $ARGUMENTS

Supported target formats:
- **Directory path:** Assess all source/config in the directory
- **File path:** Assess a single file
- **`--diff`:** Assess only files changed vs the default branch (from the merge-base to HEAD)
- **`--commit [sha]`:** Assess only files changed in a specific commit
- **`--pr [number]`:** Assess files changed in a GitHub PR (requires gh CLI)

When in diff mode, the Attacker should focus on the CHANGED lines but read surrounding context to understand the full attack surface. Pay extra attention to:
- New entry points, routes, parameters, or permissions introduced by the change
- Removed or weakened guards (auth checks, validation, rate limits) without replacement
- Changes that widen a trust boundary (new external input reaching a sensitive sink)

If no target was specified, assess the current working directory.

## Execution Steps

Follow these steps in exact order. Each agent runs as a separate subagent to ensure context isolation.

**Marker rule (applies to the Attacker, Blue Team and Arbiter).** Each agent must wrap its result in its start and end markers. If a reply lacks either marker, run that stage once more with the same input. If the second reply also lacks them, stop and tell the user which stage failed. With parallel Attackers, re-run only the Attacker that failed.

### Step 1: Read the prompt files

Read these files using the skill directory variable:
- `${CLAUDE_SKILL_DIR}/prompts/attacker.md`
- `${CLAUDE_SKILL_DIR}/prompts/blue-team.md`
- `${CLAUDE_SKILL_DIR}/prompts/arbiter.md`

### Step 1.1: Resolve the target

Run this script. Run it, do not read it.

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/resolve_target.py --profile-name .red-team-profile.md --ext ts,js,py,go,rs,rb,java,swift,kt,c,cpp,h,tf,yaml,yml,sql --name Dockerfile -- <target>
```

`<target>` is the assessment target given above, in one of the supported formats: a path (quote it if it has spaces), `--diff`, `--commit <sha>`, `--pr <number>`, or nothing at all for the current directory. If the user described the target in words instead of giving a path, find the folder or file they mean first and pass that path.

It prints one JSON object on stdout and logs its steps to stderr. It never changes the working tree. The fields this skill uses:
- `mode`: `path`, `diff`, `commit` or `pr`.
- `target`: the absolute path assessed in path mode; null in the diff modes.
- `repo_root`: the git repository root, or null.
- `file_count`: how many source and config files are in scope, after skipping tests, specs and `node_modules`, `vendor`, `dist`, `__pycache__` and `.git`.
- `files_list`: a text file with one in-scope file per line.
- `diff_file`: in the three diff modes, the saved unified diff; null in path mode.
- `checked_out`: whether the files under review match what is checked out. HEAD may differ from the commit under review if those files are unchanged since it. `head` is the current commit, `expected_head` the commit under review. Null in path mode.
- `profile`: the absolute path of the nearest `.red-team-profile.md` above the target, or null.
- `notes`: plain-language warnings, such as how many changed files the filters skipped. Pass them on to the user.
- `base`, `commit`, `pr`: the base branch, commit or PR number, when relevant.

**If the script exits with code 2:** show its error message to the user and stop.

**If `checked_out` is false:** stop before launching any agent. Tell the user in one or two plain sentences what is checked out and what is under review. Give the command that fixes it: `gh pr checkout <n>` for a PR; for a commit, check that commit out, or re-run on the current code. Never change the working tree yourself.

### Step 1.2: Load profile (if present)

If `profile` is set, read that file. The profile contains:
- **Crown jewels** — the specific assets this project must protect (e.g., "customer PII in `users` table", "the signing key", "ability to move funds"). The Attacker treats reaching these as the highest-value objective.
- **In-scope attacker personas** — which threat actors are realistic for this deployment (e.g., "unauthenticated internet", "authenticated low-privilege tenant", "compromised CI runner"). Out-of-scope personas (e.g., "physical access", "malicious root") can be declared so the Attacker doesn't waste budget on them.
- **Deployed controls** — mitigations that exist *outside the code* and should inform the Blue Team (e.g., "WAF strips `../`", "service runs in a network-isolated VPC", "secrets injected at runtime, never in repo").
- **Severity overrides** — domain-specific escalations (e.g., "any path to fund movement → Critical regardless of preconditions").

**How to apply the profile:**
- Prepend the profile's crown jewels and in-scope personas to the Attacker's task (BEFORE the generic threat-modeling section).
- Prepend the deployed controls to the Blue Team's task (it must weigh these when judging exploitability).
- Prepend severity overrides to both the Attacker's and Arbiter's tasks (they take precedence over generic calibration).
- If no profile is found, the Attacker derives crown jewels and personas itself during reconnaissance, and the Blue Team assumes only controls visible in the code.

### Step 1.5: Scope assessment and stack detection

**Resolve the target file list:**

If the target uses `--diff`, `--commit`, or `--pr`, pass the explicit file list from `files_list` to the Attacker instead of a directory. Also pass the path in `diff_file`, and tell the Attacker to read the diff first, then the surrounding code.

**If `file_count` is 0:** Report "no attack surface in scope" immediately and skip directly to Step 5. No agents need to run — a `--diff` with no changes is a no-op assessment, not a secure system.

**Use `file_count` to size the assessment:**

- **If > 50 files:** Run multiple Attacker agents in parallel by dispatching multiple Agent calls in parallel, each assigned a different **trust zone or entry-point class** (e.g., one for the public API surface, one for auth/session, one for data access, one for infra/CI/config). Merge their attack paths before passing to the Blue Team.
- **If > 150 files:** Run a quick recon agent first to map entry points and trust boundaries, then assign Attackers only to the highest-value zones (anything reachable by an unauthenticated or low-privilege persona, and anything adjacent to a crown jewel).
- **If > 200 files:** Always recon-first. Map external input sources → sensitive sinks, identify which modules sit on a trust boundary, and dispatch parallel Attackers scoped to those modules only. Do not attempt to assess the whole tree in one pass.
  _Rationale:_ Attack surface is concentrated at trust boundaries. A targeted assessment of boundary code produces higher-fidelity attack paths than a uniform sweep, and avoids context exhaustion.

**Detect the project stack and shape:**

```bash
ls <dir>/package.json <dir>/tsconfig.json <dir>/Cargo.toml \
   <dir>/go.mod <dir>/pyproject.toml <dir>/requirements.txt \
   <dir>/Gemfile <dir>/pom.xml <dir>/Package.swift \
   <dir>/Dockerfile <dir>/*.tf <dir>/openapi.* 2>/dev/null
```

`<dir>` is `target` in path mode (for a single file, its folder), and `repo_root` in the diff modes.

Determine the **project shape** — this decides which attack surfaces dominate:
network service / web app, CLI or desktop tool, library / SDK, long-running daemon, data pipeline / batch job, infrastructure-as-code. Append the relevant shape-specific surface checklist to the Attacker's task (see `attacker.md`). A project can have more than one shape (e.g., a service that also ships a CLI) — include all that apply.

### Step 2: Run the Attacker Agent

Dispatch a new isolated Agent subagent (subagent_type: general-purpose) — or multiple parallel Agent calls for large codebases:
- task: Include the attacker prompt text AND the assessment target path AND the detected project shape(s) AND any profile crown jewels/personas and severity overrides. In the diff modes, also include the `files_list` and the `diff_file` path with the instruction to read the diff first and then the surrounding code. The Attacker must use tools (Read, Bash with find/grep) to examine actual code — it may not speculate about files it hasn't read.

**Parallel-attacker ATTACK-ID namespacing:** When running multiple Attackers in parallel (per the Step 1.5 thresholds), assign each a distinct ATTACK-ID prefix so paths remain unique across the merge:

- Attacker-A (zone 1) → `ATTACK-101`, `ATTACK-102`, … (1xx)
- Attacker-B (zone 2) → `ATTACK-201`, `ATTACK-202`, … (2xx)
- Attacker-C (zone 3) → `ATTACK-301`, `ATTACK-302`, … (3xx)

Append the assigned prefix and the agent's assigned trust zone to each Attacker's task (e.g. "You are Attacker-A, assigned the public API surface. Prefix all attack paths with ATTACK-1xx."). When merging, preserve each namespace verbatim — do NOT renumber. Cross-zone **chains** (a path that starts in zone A and pivots into zone B) should be reported by whichever Attacker discovered the entry step, and noted as a chain so the Blue Team evaluates the full path.

In single-Attacker mode, no prefix is assigned and it uses default `ATTACK-1`, `ATTACK-2`, … numbering.

Wait for the Attacker to complete and capture its full output. Extract the content between `===ATTACKER_FINDINGS_START===` and `===ATTACKER_FINDINGS_END===`. This is the structured attack-path payload. If either marker is missing, apply the marker rule.

### Step 2b: Check for findings

If the Attacker reported TOTAL ATTACK PATHS: 0, skip Steps 3–4 and go directly to Step 5 with a clean report. No need to run Blue Team and Arbiter on zero paths.

### Step 3: Run the Blue Team Agent

Dispatch a new isolated Agent subagent (subagent_type: general-purpose):
- task: Include the blue-team prompt text, any profile-declared deployed controls, AND the Attacker's structured findings (content between the `===ATTACKER_FINDINGS_START===` / `===ATTACKER_FINDINGS_END===` delimiters). Do NOT include narrative or methodology text — only the structured attack paths.

The Blue Team is the defender. It must independently read the code and rule each path **DEFENDED** or **EXPLOITABLE**: are the preconditions satisfiable by the named persona? Does an existing control (auth check, validation, sandbox, network boundary, rate limit) stop a link in the chain? Is the asset actually reachable?

Extract the content between `===BLUE_TEAM_REPORT_START===` and `===BLUE_TEAM_REPORT_END===`. If either marker is missing, apply the marker rule.

### Step 4: Run the Arbiter Agent

Dispatch a new isolated Agent subagent (subagent_type: general-purpose):
- task: Include the arbiter prompt text AND any profile-declared severity overrides AND both:
  - The Attacker's full findings (between `===ATTACKER_FINDINGS_START===` / `===ATTACKER_FINDINGS_END===`)
  - The Blue Team's full report (between `===BLUE_TEAM_REPORT_START===` / `===BLUE_TEAM_REPORT_END===`)

The Arbiter must independently read the code to make final judgments on exploitability, severity, and remediation.

Extract the content between `===ARBITER_REPORT_START===` and `===ARBITER_REPORT_END===` delimiters. If either marker is missing, apply the marker rule.

### Step 5: Give the short answer first

If you skipped to this step because no files were in scope or the Attacker found no paths, there is no Arbiter report: say so in one line and stop.

Otherwise take only the text between `===ARBITER_REPORT_START===` and `===ARBITER_REPORT_END===`. Keep it in context; do not print it.

Reply in chat with:
1. One line of counts: paths reported, mitigated, dismissed, confirmed exploitable (Critical / High / Medium / Low).
2. A numbered list of the exploitable paths, worst first, one line each, at most 10:
   `1. api/upload.py:88 · anyone logged in can read another user's files by changing the id in the URL · Critical`
   Give the `file:line` where the path starts when the Arbiter's entry has one; otherwise name the entry point.
3. If there are more than 10, one more line per severity that groups the rest.
4. One line naming any mitigated path that is one control away from being live (the fragile controls in the "Mitigated" section).
5. One closing sentence: "Say 'look at 2' for the attack path and a fix, 'what stopped the rest' for the paths that were blocked, or 'full report' for everything."

Write no file unless asked. If zero paths were confirmed exploitable, say so plainly, then list each attack that was tried and the control that stopped it, one line apiece. That list is the most useful output of a clean assessment.

**"Look at N."** Print a card for that path from the Arbiter's entry: **Attack path** (the steps, in plain words), **Asset at risk**, **Why it works**, **Fix**, **How to check it worked** (a test to add or a command to run; say when this is your suggestion and not the Arbiter's), then the evidence. Plain words, short sentences.

**"What stopped the rest."** List the mitigated and dismissed paths with the control that defeats each.

**"Full report."** Print the Arbiter's report as written.
