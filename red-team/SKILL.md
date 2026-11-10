---
name: red-team
description: "Run an adversarial red-team assessment on your own codebase. Uses three isolated stages with independent coverage synthesis (Attacker, Blue Team, Arbiter) to enumerate attack surface, construct realistic attack paths, and verify which are actually exploitable. Invoke when the user asks for a red-team, threat model, attack-surface review, or adversarial security assessment of a project they own or are authorized to test — for line-level bug or vulnerability hunting use bug-hunt."
argument-hint: "[path/to/assess] [--diff] [--commit sha] [--pr number]"
---

# Red Team — Adversarial Attack-Path Assessment

Run a three-stage adversarial assessment of code **you own or are explicitly authorized to test**. An Attacker enumerates realistic paths from a persona to an asset, a Blue Team checks current defenses, and an Arbiter independently decides every path. An independent Attacker-stage coverage/chain synthesis pass always runs before the Blue Team.

This is a **paper assessment** of source, configuration, and infrastructure-as-code. Agents may read files and use read-only local search. They must not send traffic, execute exploit payloads, run the project against real infrastructure, exfiltrate data, modify the target, or change a deployed environment. Reproduction reasoning and minimal proof-of-concept sketches are allowed; weaponized exploit code is not.

## Target and rules of engagement

The target is `$ARGUMENTS`. Supported forms are a directory, one file, `--diff`, `--commit <sha>`, `--pr <number>`, or empty for the current directory. If the user described a path in words, locate it first. Confirm scope before assessing what appears to be a third-party tree.

For diff assessments, focus on changed behavior while reading enough surrounding code to trace complete paths. Include deleted code from the unified diff when its removal weakens a guard. Supply-chain-only and configuration-only changes remain in scope.

**Redaction rule for every agent and every handoff:** never copy a credential, token, private key, password, connection string, session value, or other secret value into evidence, metadata, prompts, or output. Cite its file:line and identifier, replace the value with `[REDACTED]`, and discuss only its type and exposure. This applies even when the value is visible in source or a profile.

## 1. Read prompts and resolve the target

Read:
- `${CLAUDE_SKILL_DIR}/prompts/attacker.md`
- `${CLAUDE_SKILL_DIR}/prompts/synthesis.md`
- `${CLAUDE_SKILL_DIR}/prompts/blue-team.md`
- `${CLAUDE_SKILL_DIR}/prompts/arbiter.md`

Run the resolver; do not read it:

```bash
python3 -I ${CLAUDE_SKILL_DIR}/scripts/resolve_target.py \
  --profile-name .red-team-profile.md \
  --ext ts,tsx,js,jsx,mjs,cjs,mts,cts,py,go,rs,rb,java,swift,kt,c,cc,cpp,cxx,h,hh,hpp,hxx,tf,yaml,yml,json,toml,xml,sql,cs,php,sh,bash,zsh,fish,ps1,vue,svelte,graphql,proto \
  --name Dockerfile --name Makefile --name go.mod --name go.sum \
  --name requirements.txt --name Gemfile --name Gemfile.lock \
  --name Cargo.lock --name package-lock.json --name yarn.lock \
  --name pnpm-lock.yaml --name poetry.lock --name Pipfile \
  --name Pipfile.lock --name composer.lock --name .env \
  --name .env.example --name .env.local --name .env.production \
  -- <target>
```

Quote `<target>` when needed. For no argument, omit everything after `--`. The resolver writes JSON to stdout and step logs to stderr. Exit 2 means show the actionable error and stop.

Use these resolver fields:
- `mode`, `target`, `repo_root`, `base`, `commit`, and `pr` identify the assessment.
- `assessment_root` is the directory agents review. In diff/commit/PR mode, `snapshot_root` is the immutable temp-index snapshot of the expected revision; use it rather than the working tree. Escaping symlinks may be neutralized as link-text files, with details in `notes`.
- `scope_file` is the JSON scope manifest. Every entry has a target-relative `path`, `old_path`, `status`, `in_scope`, `reason`, and `review_path`. Read only existing `review_path` files. A deleted entry has `review_path: null`; its old content is in `diff_file`.
- `diff_file` is the restricted unified diff, or null in path mode. Read it first in diff modes.
- `file_count` is the number of readable in-scope files. `change_count` is the number of changed in-scope paths, including deletions, and is null in path mode.
- `checked_out`, `head`, and `expected_head` are informational. **Never stop merely because `checked_out` is false**; the snapshot is authoritative.
- `profile` is the nearest profile or null. `notes` contains scope/filter/snapshot warnings that must reach coverage limitations and the user.

An empty **path** assessment is exactly `file_count == 0`. An empty **diff/commit/PR** assessment is exactly `change_count == 0`. In either case, stop without agents, but state the actual condition: the target contained no supported readable files, the revision had no changes, or every changed file was unsupported/filtered. Include resolver `notes` and the available file/change/filter counts in that no-op response. If only unsupported/filtered files changed, say exactly that rather than making a generic “no attack surface” claim. An empty or filtered no-op is not proof of security. If `change_count > 0` but `file_count == 0`, continue with the manifest, deletions/diff, and limitations rather than treating it as empty.

## 2. Build one common assessment packet

If `profile` is set, read it. It may define crown jewels, in-scope/out-of-scope personas, deployed controls, and severity overrides. Without a profile, the Attacker infers assets/personas and other stages rely only on controls visible in code/config.

Give **the same packet to every recon, Attacker, synthesis, Blue Team, and Arbiter agent**:
- authorization and the read-only/no-live-attack/redaction rules above;
- `mode`, `target`, `repo_root`, `assessment_root`, `snapshot_root`, and `scope_file`;
- `diff_file`, `base`, `commit`, `pr`, `head`, and `expected_head` where relevant;
- resolver `notes`, `file_count`, and `change_count`;
- detected project shape(s) and stack(s);
- all applicable profile crown jewels, personas, deployed controls, and severity overrides.

Do not give one stage a different target or a subset of the profile. Deployed controls are evidence for Blue/Arbiter, not a reason for Attacker/synthesis to omit a candidate path. A severity override takes precedence over generic calibration.

Determine shape from manifests and entry points: network service/web app, CLI/desktop, library/SDK, daemon, data pipeline/batch, or infrastructure-as-code. Multiple shapes may apply.

## 3. Prepare bounded validated handoffs

`report.py` is the only payload parser, validator, merger, counter, and renderer. Run it; do not read it. Create a temporary work directory outside the target and retain it for follow-up requests:

```bash
WORK=$(mktemp -d "${TMPDIR:-/tmp}/red-team-report.XXXXXX")
```

Save each agent's raw reply as `$WORK/<stage>-<zone-or-batch>.raw`. Validated files are ordinary unmarked JSON. Do not concatenate growing raw prose into later prompts: pass the common packet, validated JSON paths, and only the IDs assigned to that agent. Agents read the records and source themselves.

For each raw stage output, run the applicable validation command below. The tool requires exactly one start/end marker pair around one JSON object and rejects malformed, truncated, missing, duplicate, extra, or inconsistent records with a field-level error. On a validation error, rerun only that agent once with the same input plus the tool's error. If the second attempt fails, stop and name the stage and validation error. There are no manual totals or prose reports to inspect.

## 4. Stage 1A — Attacker agents

Dispatch one isolated general-purpose Attacker for a small assessment. For a large one, dispatch independent Attackers in parallel by trust zone or entry-point class (public API, auth/session, data access, workflow/state transitions, infrastructure/CI, dependency/build, and so on).

Scaling rules:
- **More than 50 files:** use multiple Attackers with disjoint zones.
- **More than 150 files:** run read-only recon first, map entry points and trust boundaries, and prioritize unauthenticated/low-privilege routes and crown-jewel-adjacent zones.
- **More than 200 files:** always recon-first. Assess the highest-value boundary zones rather than pretending to sweep the whole tree.
- Split any zone that is too large for one bounded read into smaller named zones. Do not silently drop files or findings. Put every deferred/skipped zone and reason in Attacker coverage and the final report.

Each task contains the Attacker prompt, common packet, and a distinct arbitrary-length zone label and ID slug such as `PUBLIC_API`, `AUTH`, or `ORDER_WORKFLOW`. IDs are `ATTACK-<ZONE_SLUG>-<positive integer>`; there are no numeric range reservations and no A/B/C zone limit. A single Attacker may use `ATTACK-1`, `ATTACK-2`, etc. Paths crossing zones retain the discovering path's zone and use code-level `chain_links`.

Validate every raw Attacker reply:

```bash
python3 -I ${CLAUDE_SKILL_DIR}/scripts/report.py validate attacker "$WORK/attacker-PUBLIC_API.raw" > "$WORK/attacker-PUBLIC_API.json"
```

Merge one or more validated Attacker payloads. Preserve every original ID and path zone:

```bash
python3 -I ${CLAUDE_SKILL_DIR}/scripts/report.py merge \
  "$WORK/attacker-PUBLIC_API.json" "$WORK/attacker-AUTH.json" \
  > "$WORK/attacks-initial.json"
```

The merge unions metadata/coverage and rejects duplicate IDs and self/dangling cross-path references. Zero-path zones must still be merged so their coverage survives.

## 5. Stage 1B — independent coverage and chain synthesis

Always dispatch a new isolated general-purpose synthesis agent, including when the initial merged payload has no paths. Give it the synthesis prompt, the Attacker output contract, the common packet, and `attacks-initial.json`.

It independently audits the scope manifest for missed entry points, business workflows/state invariants, deleted guards, supply-chain/build surfaces, and skipped zones. It also connects `capabilities_gained` in one zone to `capabilities_required` in another. Its new paths use `ATTACK-SYNTHESIS-<n>` and zone `SYNTHESIS`; it may reference known merged IDs in chain links. It must emit coverage even when it adds no paths.

Validate and merge synthesis **before any Blue Team run**:

```bash
python3 -I ${CLAUDE_SKILL_DIR}/scripts/report.py validate attacker \
  "$WORK/synthesis.raw" > "$WORK/synthesis.json"
python3 -I ${CLAUDE_SKILL_DIR}/scripts/report.py merge \
  "$WORK/attacks-initial.json" "$WORK/synthesis.json" \
  > "$WORK/attacks-merged.json"
```

Only the final validated merged payload gates the clean result. If its `paths` is empty (the merge log says `merged 0 attack paths`), skip Blue Team and Arbiter. Reply: **no attack paths remained after independent coverage and chain synthesis**, then list assessed zones, skipped zones, and all coverage notes/limitations from the merged payload. State that Blue/Arbiter were skipped because there were no records. Do not call the system secure.

## 6. Stage 2 — Blue Team

Give each Blue Team agent the Blue Team prompt, common packet, and `attacks-merged.json`. For up to 20 paths, assign all of them:

```bash
python3 -I ${CLAUDE_SKILL_DIR}/scripts/report.py validate blue \
  "$WORK/blue-001.raw" --attacks "$WORK/attacks-merged.json" \
  > "$WORK/blue-001.json"
```

For more than 20 paths, use disjoint batches of at most 20 IDs. Pass only each batch's IDs and records to its agent, then validate with the same repeated `--id` arguments:

```bash
python3 -I ${CLAUDE_SKILL_DIR}/scripts/report.py validate blue \
  "$WORK/blue-001.raw" --attacks "$WORK/attacks-merged.json" \
  --id ATTACK-PUBLIC_API-1 --id ATTACK-SYNTHESIS-2 \
  > "$WORK/blue-001.json"
```

Every incoming ID must be evaluated exactly once across all batches. A demonstrated **current bypass** is `EXPLOITABLE`; a currently effective but fragile control is `DEFENDED` with residual concern; unresolved evidence is `UNDETERMINED`.

## 7. Stage 3 — Arbiter

For up to 20 paths, give one new isolated general-purpose Arbiter the Arbiter prompt, common packet, `attacks-merged.json`, and every validated Blue Team batch. It emits one complete payload with every incoming ID and every severity. Validate with repeated `--blue` in any batch order:

```bash
python3 -I ${CLAUDE_SKILL_DIR}/scripts/report.py validate arbiter \
  "$WORK/arbiter.raw" \
  --attacks "$WORK/attacks-merged.json" \
  --blue "$WORK/blue-001.json" --blue "$WORK/blue-002.json" \
  > "$WORK/arbiter.json"
```

For more than 20 paths, use **disjoint Arbiter batches of at most 20 IDs**, matching the Blue Team partitions where practical. Give each Arbiter the common packet, the validated payload paths, and only its assigned Attacker records and Blue Team evaluations. It reads and emits only those IDs, with complete records at every severity and the coverage it can support. Validate each batch with repeated `--id` and the relevant `--blue` files:

```bash
python3 -I ${CLAUDE_SKILL_DIR}/scripts/report.py validate arbiter \
  "$WORK/arbiter-001.raw" \
  --attacks "$WORK/attacks-merged.json" \
  --blue "$WORK/blue-001.json" \
  --id ATTACK-PUBLIC_API-1 --id ATTACK-SYNTHESIS-2 \
  > "$WORK/arbiter-001.json"
```

Combine all validated batches into the final report:

```bash
python3 -I ${CLAUDE_SKILL_DIR}/scripts/report.py merge-arbiters \
  --attacks "$WORK/attacks-merged.json" \
  --blue "$WORK/blue-001.json" --blue "$WORK/blue-002.json" \
  "$WORK/arbiter-001.json" "$WORK/arbiter-002.json" \
  > "$WORK/arbiter.json"
```

`merge-arbiters` rejects duplicate, missing, or unexpected IDs, unions assessed/skipped coverage by zone and file plus limitations, recomputes all verdict/severity counts from records, and validates the complete result against every Attacker and Blue Team ID. Single-batch validation likewise requires exact incoming ID sets, unchanged Attacker fields, complete per-ID records, final file-level coverage with no disappeared zones, and counts reconciled to `true_severity`.

Save the final `arbiter.json`; all follow-ups render from it, never from memory, an individual batch, or missing prose.

## 8. Short answer first and follow-ups

Render and return the summary:

```bash
python3 -I ${CLAUDE_SKILL_DIR}/scripts/report.py render summary "$WORK/arbiter.json"
```

It leads with total/exploitable/mitigated/not-a-risk/undetermined counts and exploitable severity, discloses assessed/skipped coverage and limitations, lists at most ten worst exploitable paths with `file:line`, names fragile controls, and gives follow-up instructions. If nothing is exploitable but paths were attempted, it lists every attempt and its current control/reason.

Use the saved validated payload for each follow-up:

```bash
python3 -I ${CLAUDE_SKILL_DIR}/scripts/report.py render path "$WORK/arbiter.json" 2
python3 -I ${CLAUDE_SKILL_DIR}/scripts/report.py render path "$WORK/arbiter.json" ATTACK-PUBLIC_API-1
python3 -I ${CLAUDE_SKILL_DIR}/scripts/report.py render stopped "$WORK/arbiter.json"
python3 -I ${CLAUDE_SKILL_DIR}/scripts/report.py render full "$WORK/arbiter.json"
```

- **“look at N”** uses the worst-first exploitable summary index. **“look at ID”** opens any verdict or severity.
- **“what stopped the rest”** renders mitigated, dismissed, and undetermined records.
- **“full report”** renders every full record at every severity, with no invented details.

Do not write a report into the assessed repository unless the user explicitly asks for a file.
