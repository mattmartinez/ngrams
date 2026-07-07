---
name: red-team
description: "Run an adversarial red-team assessment on your own codebase. Uses 3 isolated subagents (Attacker, Blue Team, Arbiter) to enumerate attack surface, construct realistic attack paths, and verify which are actually exploitable. Invoke when the user asks for a red-team, threat model, attack-surface review, or adversarial security assessment of a project they own or are authorized to test — for line-level bug or vulnerability hunting use bug-hunt."
argument-hint: "[path/to/assess] [--diff] [--commit sha] [--pr number]"
---

# Red Team — Adversarial Attack-Path Assessment

Run a 3-agent adversarial red-team assessment on a codebase **you own or are explicitly authorized to test**. Each agent runs as an isolated subagent (in Claude Code, the Task tool with a general-purpose agent).

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
- **`--diff`:** Assess only files changed vs the default branch
  ```bash
  base=$(git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#origin/##'); git diff --name-only $(git merge-base HEAD "${base:-main}")..HEAD
  ```
- **`--commit [sha]`:** Assess only files changed in a specific commit
  ```bash
  git diff --name-only [sha]^..[sha]
  ```
- **`--pr [number]`:** Assess files changed in a GitHub PR (requires gh CLI)
  ```bash
  gh pr diff [number] --name-only
  ```

When in diff mode, the Attacker should focus on the CHANGED lines but read surrounding context to understand the full attack surface. Pay extra attention to:
- New entry points, routes, parameters, or permissions introduced by the change
- Removed or weakened guards (auth checks, validation, rate limits) without replacement
- Changes that widen a trust boundary (new external input reaching a sensitive sink)

If no target was specified, assess the current working directory.

## Execution Steps

Follow these steps in exact order. Each agent runs as a separate subagent to ensure context isolation.

### Step 1: Read the prompt files

Read these files using the skill directory variable:
- `${CLAUDE_SKILL_DIR}/prompts/attacker.md`
- `${CLAUDE_SKILL_DIR}/prompts/blue-team.md`
- `${CLAUDE_SKILL_DIR}/prompts/arbiter.md`

### Step 1.1: Load profile (if present)

Search for a `.red-team-profile.md` file by walking up from the assessment target directory:

```bash
# For a path target, start from its absolute location; for --diff/--commit/--pr
# (which are flags, not paths) start the walk from the repo root, else cwd.
if [ -e "[target]" ]; then
  dir="$(cd "[target]" 2>/dev/null && pwd || cd "$(dirname "[target]")" 2>/dev/null && pwd || echo /)"
else
  dir="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
fi
while [ "$dir" != "/" ]; do
  if [ -f "$dir/.red-team-profile.md" ]; then
    echo "Found profile: $dir/.red-team-profile.md"
    break
  fi
  prev="$dir"; dir="$(dirname "$dir")"; [ "$dir" = "$prev" ] && break
done
```

If found, read the profile file. The profile contains:
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

If the target uses `--diff`, `--commit`, or `--pr`, run the appropriate git/gh command to get the list of changed files. Pass this explicit file list to the Attacker instead of a directory.

**If the resolved file list is empty:** Report "no attack surface in scope" immediately and skip directly to Step 5. No agents need to run — a `--diff` with no changes is a no-op assessment, not a secure system.

**Count source/config files in the target:**

```bash
find [target] -type f \( -name "*.ts" -o -name "*.js" -o -name "*.py" \
  -o -name "*.go" -o -name "*.rs" -o -name "*.rb" -o -name "*.java" \
  -o -name "*.swift" -o -name "*.kt" -o -name "*.c" -o -name "*.cpp" \
  -o -name "*.h" -o -name "*.tf" -o -name "*.yaml" -o -name "*.yml" \
  -o -name "Dockerfile" -o -name "*.sql" \) | grep -v node_modules | \
  grep -v vendor | grep -v dist | grep -v __pycache__ | \
  grep -v '.test.' | grep -v '.spec.' | wc -l
```

In `--diff`/`--commit`/`--pr` modes, skip the `find` — the file count is the length of the resolved changed-file list; pipe that list through the same exclusion greps, then `wc -l`.

- **If > 50 files:** Run multiple Attacker agents in parallel by dispatching multiple Task calls in parallel, each assigned a different **trust zone or entry-point class** (e.g., one for the public API surface, one for auth/session, one for data access, one for infra/CI/config). Merge their attack paths before passing to the Blue Team.
- **If > 150 files:** Run a quick recon agent first to map entry points and trust boundaries, then assign Attackers only to the highest-value zones (anything reachable by an unauthenticated or low-privilege persona, and anything adjacent to a crown jewel).
- **If > 200 files:** Always recon-first. Map external input sources → sensitive sinks, identify which modules sit on a trust boundary, and dispatch parallel Attackers scoped to those modules only. Do not attempt to assess the whole tree in one pass.
  _Rationale:_ Attack surface is concentrated at trust boundaries. A targeted assessment of boundary code produces higher-fidelity attack paths than a uniform sweep, and avoids context exhaustion.

**Detect the project stack and shape:**

```bash
ls [target]/package.json [target]/tsconfig.json [target]/Cargo.toml \
   [target]/go.mod [target]/pyproject.toml [target]/requirements.txt \
   [target]/Gemfile [target]/pom.xml [target]/Package.swift \
   [target]/Dockerfile [target]/*.tf [target]/openapi.* 2>/dev/null
```

In `--diff`/`--commit`/`--pr` modes `[target]` is a flag, not a directory — run this `ls` against the repo root (`git rev-parse --show-toplevel`), not the changed files, since project shape is a property of the repo rather than the diff.

Determine the **project shape** — this decides which attack surfaces dominate:
network service / web app, CLI or desktop tool, library / SDK, long-running daemon, data pipeline / batch job, infrastructure-as-code. Append the relevant shape-specific surface checklist to the Attacker's task (see `attacker.md`). A project can have more than one shape (e.g., a service that also ships a CLI) — include all that apply.

### Step 2: Run the Attacker Agent

Dispatch a new isolated Task subagent (subagent_type: general-purpose) — or multiple parallel Task calls for large codebases:
- task: Include the attacker prompt text AND the assessment target path AND the detected project shape(s) AND any profile crown jewels/personas and severity overrides. The Attacker must use tools (Read, Bash with find/grep) to examine actual code — it may not speculate about files it hasn't read.

**Parallel-attacker ATTACK-ID namespacing:** When running multiple Attackers in parallel (per the Step 1.5 thresholds), assign each a distinct ATTACK-ID prefix so paths remain unique across the merge:

- Attacker-A (zone 1) → `ATTACK-101`, `ATTACK-102`, … (1xx)
- Attacker-B (zone 2) → `ATTACK-201`, `ATTACK-202`, … (2xx)
- Attacker-C (zone 3) → `ATTACK-301`, `ATTACK-302`, … (3xx)

Append the assigned prefix and the agent's assigned trust zone to each Attacker's task (e.g. "You are Attacker-A, assigned the public API surface. Prefix all attack paths with ATTACK-1xx."). When merging, preserve each namespace verbatim — do NOT renumber. Cross-zone **chains** (a path that starts in zone A and pivots into zone B) should be reported by whichever Attacker discovered the entry step, and noted as a chain so the Blue Team evaluates the full path.

In single-Attacker mode, no prefix is assigned and it uses default `ATTACK-1`, `ATTACK-2`, … numbering.

Wait for the Attacker to complete and capture its full output. Extract the content between `===ATTACKER_FINDINGS_START===` and `===ATTACKER_FINDINGS_END===`. This is the structured attack-path payload.

### Step 2b: Check for findings

If the Attacker reported TOTAL ATTACK PATHS: 0, skip Steps 3–4 and go directly to Step 5 with a clean report. No need to run Blue Team and Arbiter on zero paths.

### Step 3: Run the Blue Team Agent

Dispatch a new isolated Task subagent (subagent_type: general-purpose):
- task: Include the blue-team prompt text, any profile-declared deployed controls, AND the Attacker's structured findings (content between the `===ATTACKER_FINDINGS_START===` / `===ATTACKER_FINDINGS_END===` delimiters). Do NOT include narrative or methodology text — only the structured attack paths.

The Blue Team is the defender. It must independently read the code and rule each path **DEFENDED** or **EXPLOITABLE**: are the preconditions satisfiable by the named persona? Does an existing control (auth check, validation, sandbox, network boundary, rate limit) stop a link in the chain? Is the asset actually reachable?

Extract the content between `===BLUE_TEAM_REPORT_START===` and `===BLUE_TEAM_REPORT_END===`.

### Step 4: Run the Arbiter Agent

Dispatch a new isolated Task subagent (subagent_type: general-purpose):
- task: Include the arbiter prompt text AND any profile-declared severity overrides AND both:
  - The Attacker's full findings (between `===ATTACKER_FINDINGS_START===` / `===ATTACKER_FINDINGS_END===`)
  - The Blue Team's full report (between `===BLUE_TEAM_REPORT_START===` / `===BLUE_TEAM_REPORT_END===`)

The Arbiter must independently read the code to make final judgments on exploitability, severity, and remediation.

### Step 5: Present the Final Report

Display the Arbiter's final verified assessment to the user. The report must use the detailed format for Critical and High paths — each as its own section with **Attack path** (the kill chain), **Asset at risk**, **Why it's exploitable**, and **Remediation**. Lower-severity items can use a compact table.

Include:
1. Summary stats (paths reported, mitigated, confirmed exploitable; counts by severity)
2. Critical paths (detailed format, each as its own subsection)
3. High paths (detailed format)
4. Medium/Low paths (compact table)
5. Theoretical-but-not-currently-exploitable items flagged for awareness (e.g., one control away from being live)
6. A collapsed section with dismissed paths and *why a control defeats them* (this is itself useful — it documents what's protecting you)

If zero exploitable paths were confirmed, say so clearly — and surface the dismissed-paths section prominently, because "here is the attack we tried and the control that stopped it" is the most valuable output of a clean red team.
