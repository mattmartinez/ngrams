---
name: bug-hunt-research
description: "Autonomous prompt optimization loop for the bug-hunt skill. Iteratively modifies bug-hunt prompts (hunter, skeptic, referee), runs them against benchmark codebases with planted bugs and traps, scores with a deterministic evaluation function, and keeps or reverts changes based on improvement. Modeled on the autoresearch pattern. Invoke with /bug-hunt-research to start optimizing."
argument-hint: "[tag] [--resume] [--ideas 'hypothesis1, hypothesis2']"
disable-model-invocation: true
---

# Bug-Hunt Research — Autonomous Prompt Optimization Loop

Run an autonomous, indefinite experiment loop that modifies the bug-hunt skill's prompts, runs them against benchmark codebases with known bugs, scores the results, and keeps or discards changes based on improvement. The agent acts as a fully autonomous prompt researcher. **Never stop. Never ask permission. Run until interrupted.**

## Target

Everything lives in this repo checkout — run the loop **here**, not in `~/.claude/skills` (that copy is not a git repo, so the commit/revert engine below cannot run there). `<repo>` is your local clone of this repo. After a session, `./install.sh ~/.claude/skills` publishes the improved prompts to the live skill.

The skill files to optimize:
```
<repo>/bug-hunt/
├── SKILL.md                    # orchestration (modify sparingly)
└── prompts/
    ├── hunter.md               # primary target for optimization
    ├── skeptic.md              # secondary target
    └── referee.md              # secondary target
```

The benchmark suite and evaluation harness:
```
<repo>/bug-hunt-research/
├── benchmarks/
│   ├── easy/vuln_api_server.py        # 7 planted bugs (SQL injection, MD5, path traversal, etc.)
│   ├── medium/data_pipeline.py        # 4 planted bugs (race conditions, off-by-one, silent exceptions)
│   ├── hard/auth_session.py           # 5 planted bugs (token truncation, predictable RNG, timing attacks)
│   └── traps/safe_but_suspicious.py   # 5 false-positive traps (safe getattr, intentional sleep, etc.)
├── manifest.json                      # ground truth — kept OUT of benchmarks/ so hunters never read the answer key
├── evaluate.py                        # scoring script
├── results.tsv                        # experiment log (tracked)
└── insights.md                        # compressed memory (tracked)
```

If the user provided arguments: $ARGUMENTS

Argument formats:
- **`[tag]`** — Branch tag for this session (e.g. `mar24`). Creates branch `bug-hunt-research/<tag>`.
- **`--resume`** — Resume an existing branch instead of creating a new one.
- **`--ideas '...'`** — Seed the first experiments with specific hypotheses.

If no tag is provided, generate one from today's date.

---

## Phase 1: Setup

### 1.1 Create a branch

First confirm the working tree is clean — the loop reverts experiments with git, which must not touch unrelated uncommitted work:

```bash
cd <repo>            # your local clone of this repo
git status --porcelain --untracked-files=no   # must be empty; commit or stash first if not
git checkout -b bug-hunt-research/<tag>
```

If using `--resume`, check out the existing branch instead.

### 1.2 Read all files for full context

Read completely — do not skim:

1. **Bug-hunt prompts**: `bug-hunt/prompts/hunter.md`, `bug-hunt/prompts/skeptic.md`, `bug-hunt/prompts/referee.md`
2. **Bug-hunt orchestration**: `bug-hunt/SKILL.md`
3. **Benchmark files**: All 4 Python files in `bug-hunt-research/benchmarks/`
4. **Manifest**: `bug-hunt-research/manifest.json` — understand every planted bug and trap
5. **Evaluation script**: `bug-hunt-research/evaluate.py` — understand how scoring works

### 1.3 Initialize tracking

`results.tsv` and `insights.md` live in `bug-hunt-research/` and are **tracked** — they are cross-session memory. Create them only if missing; otherwise append, never overwrite.

`results.tsv` header (tab-separated):

```
commit	composite	f1	recall	precision	severity_accuracy	trap_resistance	description
```

### 1.4 Run baseline

Run the current unmodified bug-hunt prompts against all benchmarks and score them. This is your baseline. Record in results.tsv.

**How to run a single evaluation:**

1. Dispatch the 3-agent bug-hunt pipeline (hunter → skeptic → referee) as isolated Task subagents against the benchmark directory `bug-hunt-research/benchmarks/`. Never give a hunter the manifest — it is the answer key.
2. Capture the referee's final output to a temp file
3. Run: `python3 bug-hunt-research/evaluate.py <referee_output_file> bug-hunt-research/manifest.json`
4. Record the composite score and component scores

### 1.5 Begin experiments

Once baseline is recorded, immediately begin the experiment loop. **Do not wait for user confirmation.**

---

## Phase 2: The Experiment Loop

```
LOOP FOREVER:
    1. Read insights.md, check recent results
    2. Hypothesize ONE prompt change; edit the file(s) in bug-hunt/prompts/
    3. Run the full bug-hunt pipeline against the benchmarks; score with evaluate.py
    4. Append the result row to results.tsv
    5. Decide (see Phase 3):
         keep    → git add bug-hunt/ bug-hunt-research/results.tsv bug-hunt-research/insights.md
                   git commit -m "experiment: <desc> (keep, composite X)"
         discard → git checkout -- bug-hunt/prompts/          # revert the prompt edit ONLY
                   git add bug-hunt-research/results.tsv
                   git commit -m "log: <desc> (discard, composite X)"
    6. Update insights.md every ~3 experiments
    7. GOTO 1

Never use `git commit -am` or `git reset --hard` — both reach beyond bug-hunt/ and
can destroy the results log or unrelated work.
```

### Running the Bug-Hunt Pipeline

For each experiment, run the full 3-agent pipeline as isolated Task subagents:

**Step A — Hunter**: Launch a Task subagent (subagent_type: general-purpose) with the current `hunter.md` prompt content plus the benchmark directory path (`bug-hunt-research/benchmarks/`). The hunter must read the actual benchmark files — never hand it the manifest.

**Step B — Skeptic**: Launch a new Task subagent with the current `skeptic.md` prompt content plus the hunter's structured findings.

**Step C — Referee**: Launch a new Task subagent with the current `referee.md` prompt content plus both the hunter's and skeptic's reports.

**Step D — Score**: Save the referee output to a temp file, run `evaluate.py`, parse the JSON scores.

### Key Rules

- **Capture subagent output**: Save the full text output from each Task subagent call.
- **One change at a time**: Don't modify all 3 prompts simultaneously. Change hunter OR skeptic OR referee in a single experiment.
- **Crash handling**: If a subagent fails or produces unparseable output, log as crash, revert, move on.
- **Each experiment takes ~5-10 minutes**: Budget accordingly.

---

## Phase 3: Decision Making

### Keep vs Discard

Run-to-run noise is ~0.06 (one bug found/missed = 1/16 ≈ 0.0625), so a single run cannot resolve small deltas.

| Condition | Action | Status |
|-----------|--------|--------|
| Composite improved by ≥0.07 on one run | Keep, advance branch | `keep` |
| Composite delta <0.07 (either sign) | Re-run once; keep only if the second run also meets or beats the parent's score (record both rows) | `keep`/`discard` |
| Composite clearly worse (≥0.07 drop) | Revert the prompt edit | `discard` |
| Prompt strictly simpler at equal-or-better score | Keep — simplification win | `keep` |
| Run crashed or output unparseable | Fix ONE trivial issue and re-run once, else revert | `crash` |

### The Simplicity Criterion

Simpler prompts are better, all else equal:
- Shorter prompts use less context window
- Fewer instructions means less confusion
- A 0.01 improvement from deleting prompt text? Definitely keep.

---

## Phase 4: Experiment Strategy

### Initial Hypotheses (try these first)

These are the highest-signal changes to explore early:

1. **Hunter false-positive penalty** — Add a small cost for false positives (currently "costs you nothing"). Test: "-0.5 per disproven finding", "-1 per disproven finding"
2. **Hunter priority guidance** — Add "examine security-critical code first" or "prioritize files with user input handling"
3. **Skeptic threshold calibration** — Test 60%, 70%, 75% confidence thresholds vs current 67%
4. **Hunter category expansion** — Add supply-chain, configuration, dependency categories
5. **Cross-agent information** — Pass hunter's methodology notes to skeptic, not just findings
6. **Referee severity calibration** — Add explicit severity criteria with examples
7. **Hunter scope management** — Add guidance for large codebases (file count limits, triage order)
8. **Skeptic code-reading depth** — Require skeptic to read N lines of surrounding context, not just the cited lines

### Category Diversity

Rotate across these categories. **If your last 3 experiments targeted the same prompt, switch.**

| Category | Target | Examples |
|----------|--------|----------|
| **Hunter accuracy** | hunter.md | Scoring incentives, category lists, search strategies |
| **Skeptic calibration** | skeptic.md | Confidence thresholds, evidence requirements, risk formulas |
| **Referee judgment** | referee.md | Severity criteria, verdict formatting, tie-breaking rules |
| **Orchestration** | SKILL.md | Information passing, agent ordering, context management |
| **Simplification** | Any | Remove redundant instructions, tighten wording |

### Plateau Detection

If **5 consecutive experiments** show no improvement (composite delta < 0.07, i.e. within noise):
- Stop tweaking the same prompt — switch targets
- Try structural changes (e.g., add a 4th agent, change information flow)
- Combine previous near-improvements
- Try removing instructions instead of adding them

---

## Phase 5: Memory & Logging

### results.tsv

Tab-separated, 8 columns. Append after every experiment:

```
commit	composite	f1	recall	precision	severity_accuracy	trap_resistance	description
a1b2c3d	0.000000	0.0000	0.0000	0.0000	0.0000	0.0000	baseline
```

`composite = f1 × (0.5 + 0.5·severity_accuracy) × trap_resistance` (formula v2, 2026-07 — severity is a 0.5–1.0 multiplier so it can't zero out real detection). Older rows use a 7-column schema and `f1 × severity_accuracy × trap_resistance`; their composites are not comparable, so re-baseline before comparing across the change.

### insights.md

Update every ~3 experiments. Overwrite stale entries. Keep under ~50 lines.

Contents:
1. **Best result**: current best composite, commit, description
2. **What works**: changes that improved scores
3. **What doesn't work**: changes that hurt or had no effect
4. **Component analysis**: which sub-score (recall, precision, trap_resistance) is the bottleneck?
5. **Next hypotheses**: 2-3 untried ideas

**Always re-read insights.md before proposing each new experiment.**

---

## Phase 6: Publish & caveats

- **Deploy after a session** (or after each solid `keep`): run `./install.sh ~/.claude/skills` from `<repo>` to copy the improved prompts into the live skill. Edits here are inert until you do.
- **Benchmark saturation**: only 16 planted bugs across 4 files. A strong model finds real bugs the manifest doesn't list; `manifest.json` carries a `neutral` list so those don't count against precision — extend it as you adjudicate new real extras. Consider a held-out `benchmarks/holdout/` set (its own manifest, ideally a second language) scored only at session end and never used for keep/discard, so gains reflect generalization, not memorization.

---

## Constraints — Hard Rules

1. **Only modify the prompts in `bug-hunt/`**. Never modify benchmark files, `manifest.json`, or `evaluate.py`.
2. **The scoring function is ground truth**. If the bug-hunt doesn't find a planted bug, that's a bug-hunt problem, not a manifest problem.
3. **Prompt edits must describe general bug classes** — never reference a specific planted bug, its file, its line numbers, or its manifest severity. Encoding the answer key into a prompt inflates the score without improving real bug-hunting.
4. **One variable at a time**. Each experiment changes ONE thing about ONE prompt.
5. **Never stop to ask** — the human may be asleep — UNLESS the environment is broken (benchmarks or `evaluate.py` missing, git unavailable): then write a note to insights.md and stop.
6. **results.tsv and insights.md are tracked** — commit them with each experiment (Phase 2). Never `git commit -am` or `git reset --hard`; both reach beyond `bug-hunt/`.

---

## Autonomy Protocol

**NEVER STOP.** Once the experiment loop begins:

- Do NOT pause to ask the human if you should continue.
- Do NOT ask "should I keep going?" or "is this a good stopping point?"
- If you run out of ideas, re-read all prompt files from scratch, re-read insights.md, try combining successes, try radical restructuring, try deletion.

At ~5-10 minutes per experiment, you run ~6-12 experiments/hour. An overnight session produces ~50-100 prompt variations tested. The user wakes up to optimized bug-hunt prompts.
