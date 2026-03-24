---
name: bug-hunt-research
description: "Autonomous prompt optimization loop for the bug-hunt skill. Iteratively modifies bug-hunt prompts (hunter, skeptic, referee), runs them against benchmark codebases with planted bugs and traps, scores with a deterministic evaluation function, and keeps or reverts changes based on improvement. Modeled on the autoresearch pattern. Invoke with /bug-hunt-research to start optimizing."
argument-hint: "[tag] [--resume] [--ideas 'hypothesis1, hypothesis2']"
---

# Bug-Hunt Research — Autonomous Prompt Optimization Loop

Run an autonomous, indefinite experiment loop that modifies the bug-hunt skill's prompts, runs them against benchmark codebases with known bugs, scores the results, and keeps or discards changes based on improvement. The agent acts as a fully autonomous prompt researcher. **Never stop. Never ask permission. Run until interrupted.**

## Target

The skill files to optimize live in:
```
~/.gsd/agent/skills/bug-hunt/
├── SKILL.md                    # orchestration (modify sparingly)
└── prompts/
    ├── hunter.md               # primary target for optimization
    ├── skeptic.md              # secondary target
    └── referee.md              # secondary target
```

The benchmark suite and evaluation harness live in:
```
~/.gsd/agent/skills/bug-hunt-research/
├── benchmarks/
│   ├── easy/vuln_api_server.py        # 7 planted bugs (SQL injection, MD5, path traversal, etc.)
│   ├── medium/data_pipeline.py        # 4 planted bugs (race conditions, off-by-one, silent exceptions)
│   ├── hard/auth_session.py           # 5 planted bugs (token truncation, predictable RNG, timing attacks)
│   ├── traps/safe_but_suspicious.py   # 5 false-positive traps (safe getattr, intentional sleep, etc.)
│   └── manifest.json                  # ground truth for all planted bugs and traps
├── evaluate.py                        # scoring script
├── results.tsv                        # experiment log (created during setup)
└── insights.md                        # compressed memory (created during setup)
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

```bash
cd ~/.gsd/agent/skills
git checkout -b bug-hunt-research/<tag>
```

If using `--resume`, check out the existing branch instead.

### 1.2 Read all files for full context

Read completely — do not skim:

1. **Bug-hunt prompts**: `bug-hunt/prompts/hunter.md`, `bug-hunt/prompts/skeptic.md`, `bug-hunt/prompts/referee.md`
2. **Bug-hunt orchestration**: `bug-hunt/SKILL.md`
3. **Benchmark files**: All 4 Python files in `bug-hunt-research/benchmarks/`
4. **Manifest**: `bug-hunt-research/benchmarks/manifest.json` — understand every planted bug and trap
5. **Evaluation script**: `bug-hunt-research/evaluate.py` — understand how scoring works

### 1.3 Initialize tracking

Create `results.tsv` in `~/.gsd/agent/skills/bug-hunt-research/`:

```
commit	composite	f1	recall	precision	trap_resistance	description
```

Tab-separated. Stays untracked.

Create `insights.md` with initial state.

### 1.4 Run baseline

Run the current unmodified bug-hunt prompts against all benchmarks and score them. This is your baseline. Record in results.tsv.

**How to run a single evaluation:**

1. Use the `subagent` tool to run the 3-agent bug-hunt pipeline (hunter → skeptic → referee) against the benchmark directory `~/.gsd/agent/skills/bug-hunt-research/benchmarks/`
2. Capture the referee's final output to a temp file
3. Run: `python ~/.gsd/agent/skills/bug-hunt-research/evaluate.py <referee_output_file> ~/.gsd/agent/skills/bug-hunt-research/benchmarks/manifest.json`
4. Record the composite score and component scores

### 1.5 Begin experiments

Once baseline is recorded, immediately begin the experiment loop. **Do not wait for user confirmation.**

---

## Phase 2: The Experiment Loop

```
LOOP FOREVER:
    1. Read insights.md, check recent results
    2. Hypothesize a prompt change
    3. Edit the prompt file(s) in bug-hunt/prompts/
    4. git commit -am "experiment: <description>"
    5. Run full bug-hunt against benchmarks
    6. Score with evaluate.py
    7. Log to results.tsv
    8. If composite improved → keep commit (advance branch)
    9. If not improved → git reset --hard HEAD~1 (revert)
    10. Update insights.md every ~3 experiments
    11. GOTO 1
```

### Running the Bug-Hunt Pipeline

For each experiment, you need to run the full 3-agent pipeline. Use the `subagent` tool:

**Step A — Hunter**: Invoke a worker subagent with the current `hunter.md` prompt content plus the benchmark directory path. The hunter must use tools to read the actual benchmark files.

**Step B — Skeptic**: Invoke a new worker subagent with the current `skeptic.md` prompt content plus the hunter's structured findings.

**Step C — Referee**: Invoke a new worker subagent with the current `referee.md` prompt content plus both the hunter's and skeptic's reports.

**Step D — Score**: Save the referee output to a temp file, run `evaluate.py`, parse the JSON scores.

### Key Rules

- **Redirect subagent output**: Capture the full text output from each subagent call.
- **One change at a time**: Don't modify all 3 prompts simultaneously. Change hunter OR skeptic OR referee in a single experiment.
- **Crash handling**: If a subagent fails or produces unparseable output, log as crash, revert, move on.
- **Each experiment takes ~5-10 minutes**: Budget accordingly.

---

## Phase 3: Decision Making

### Keep vs Discard

| Condition | Action | Status |
|-----------|--------|--------|
| Composite improved by ≥0.01 | Keep commit, advance branch | `keep` |
| Composite improved by <0.01 but prompt is simpler | Keep — simplification win | `keep` |
| Composite improved by <0.01, adds complexity | Discard — not worth it | `discard` |
| Composite equal or worse | Revert commit | `discard` |
| Run crashed or output unparseable | Fix trivial issues or revert | `crash` |

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

If **5 consecutive experiments** show no improvement (composite delta < 0.01):
- Stop tweaking the same prompt — switch targets
- Try structural changes (e.g., add a 4th agent, change information flow)
- Combine previous near-improvements
- Try removing instructions instead of adding them

---

## Phase 5: Memory & Logging

### results.tsv

Tab-separated, 7 columns. Append after every experiment:

```
commit	composite	f1	recall	precision	trap_resistance	description
a1b2c3d	0.000000	0.0000	0.0000	0.0000	0.0000	baseline
b2c3d4e	0.125000	0.5000	0.4375	0.5833	0.8000	add FP penalty to hunter
```

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

## Constraints — Hard Rules

1. **Only modify files in `~/.gsd/agent/skills/bug-hunt/`**. Never modify benchmark files or manifest.json.
2. **Never modify `evaluate.py`**. The scoring function is sacred ground truth.
3. **The manifest is ground truth**. If the bug-hunt doesn't find a planted bug, that's a bug-hunt problem, not a manifest problem.
4. **One variable at a time**. Each experiment changes ONE thing about ONE prompt.
5. **Never stop to ask**. The human may be asleep. Run autonomously until interrupted.
6. **Never commit results.tsv or insights.md**. They stay untracked.

---

## Autonomy Protocol

**NEVER STOP.** Once the experiment loop begins:

- Do NOT pause to ask the human if you should continue.
- Do NOT ask "should I keep going?" or "is this a good stopping point?"
- If you run out of ideas, re-read all prompt files from scratch, re-read insights.md, try combining successes, try radical restructuring, try deletion.

At ~5-10 minutes per experiment, you run ~6-12 experiments/hour. An overnight session produces ~50-100 prompt variations tested. The user wakes up to optimized bug-hunt prompts.
