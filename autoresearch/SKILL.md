---
name: autoresearch
description: "Autonomous ML research loop. Iteratively modifies a training script, runs experiments on a fixed time budget, and advances the branch when results improve. Runs indefinitely without human intervention — leave it overnight and wake up to results. Invoke with /autoresearch to start a new experiment session."
argument-hint: "[tag] [--resume] [--depth N] [--ideas 'hypothesis1, hypothesis2']"
---

# Autoresearch — Autonomous ML Experiment Loop

Run an autonomous, indefinite experiment loop that modifies a training script, measures results against a fixed evaluation metric, and keeps or discards changes based on improvement. The agent acts as a fully autonomous researcher: if an idea works, advance; if it doesn't, revert and try something new. **Never stop. Never ask permission. Run until interrupted.**

## Target

The experiment directory is the autoresearch project. Locate it by finding `train.py` and `prepare.py` in the working tree. If the user provided arguments: $ARGUMENTS

Argument formats:
- **`[tag]`** — Branch tag for this session (e.g. `mar15`). Creates branch `autoresearch/<tag>`.
- **`--resume`** — Resume an existing autoresearch branch instead of creating a new one.
- **`--depth N`** — Override the model DEPTH hyperparameter for baseline.
- **`--ideas '...'`** — Seed the first experiments with specific hypotheses.

If no tag is provided, generate one from today's date (e.g. `mar15`, `mar15b` if taken).

---

## Phase 1: Setup

Before any experiment runs, complete every step in this checklist.

### 1.1 Agree on a run tag

Propose a tag based on today's date. Verify the branch `autoresearch/<tag>` does not already exist. If `--resume` was passed, check out the existing branch instead.

### 1.2 Create the branch

```bash
git checkout -b autoresearch/<tag>
```

### 1.3 Read all in-scope files for full context

Read these files completely — do not skim:

- **`prepare.py`** — Fixed constants, data prep, tokenizer, dataloader, evaluation harness. **Read-only. Never modify.**
- **`train.py`** — The single file you modify. Model architecture, optimizer, hyperparameters, training loop.
- **`pyproject.toml`** — Locked dependencies. **Do not add packages.**

Extract and internalize:
- `MAX_SEQ_LEN`, `TIME_BUDGET`, `EVAL_TOKENS` from prepare.py
- The evaluation function `evaluate_bpb` — understand exactly what it measures
- The model architecture (GPT with RoPE, sliding window attention, value embeddings, MuonAdamW optimizer)
- All tunable hyperparameters at the top of train.py
- The dataloader mechanics (BOS-aligned packing)

### 1.4 Verify data exists

```bash
ls ~/.cache/autoresearch/data/*.parquet | head -5
ls ~/.cache/autoresearch/tokenizer/tokenizer.json
```

If missing, tell the user to run `uv run prepare.py` and wait.

### 1.5 Initialize results tracking

Create `results.tsv` with the header row:

```
commit	val_bpb	memory_gb	status	description
```

This file is **tab-separated** (not commas). It stays untracked by git.

### 1.6 Confirm and go

Confirm setup looks good, then immediately begin experimentation. **Do not wait for user confirmation to start the loop.**

---

## Phase 2: The Experiment Loop

This is the core of autoresearch. It runs **indefinitely** until the human manually interrupts.

```
LOOP FOREVER:
    1. Plan experiment (read insights.md, check recent results)
    2. Edit train.py with the experimental change
    3. git commit -am "experiment: <description>"
    4. Run: uv run train.py > run.log 2>&1
    5. Read results (results.json or grep run.log)
    6. Log to results.tsv
    7. If improved → keep commit (advance branch)
    8. If not improved → git reset --hard HEAD~1 (revert)
    9. Update insights.md every ~5 experiments
    10. GOTO 1
```

### Detailed step-by-step

See [references/experiment-loop.md](references/experiment-loop.md) for the complete loop protocol with all edge cases.

**Key rules:**
- **Redirect all output**: `uv run train.py > run.log 2>&1` — never let training output flood your context window.
- **Read results from JSON**: `cat results.json` gives structured output. Fallback: `grep "^val_bpb:\|^peak_vram_mb:" run.log`.
- **Crash handling**: If run.log has no val_bpb and no results.json, the run crashed. Run `tail -n 50 run.log` for the traceback. Fix trivial bugs (typos, imports) and re-run. If fundamentally broken, log as crash, revert, move on.
- **Timeout**: Each experiment should take ~5 minutes of training + startup overhead. If a run exceeds 10 minutes total, kill it and treat as failure.
- **Never commit results.tsv**: It stays untracked.

---

## Phase 3: Decision Making

### Keep vs Discard

| Condition | Action | Status |
|-----------|--------|--------|
| val_bpb improved by ≥0.001 | Keep commit, advance branch | `keep` |
| val_bpb improved by <0.001 but code is simpler | Keep — simplification win | `keep` |
| val_bpb improved by <0.001, adds complexity | Discard — not worth it | `discard` |
| val_bpb equal or worse | Revert commit | `discard` |
| Run crashed | Fix trivial bugs or revert | `crash` |

### The Simplicity Criterion

All else being equal, **simpler is better**.

- A 0.001 improvement that adds 20 lines of hacky code? Probably not worth it.
- A 0.001 improvement from **deleting** code? Definitely keep.
- Equal performance with much simpler code? Keep.

### VRAM Budget

VRAM is a soft constraint. Small increases for meaningful val_bpb gains are acceptable. Dramatic blowups are not. Track `peak_vram_mb` in results and flag when memory grows more than ~20% over baseline.

---

## Phase 4: Experiment Strategy

See [references/strategy.md](references/strategy.md) for the complete strategy guide.

### Category Diversity

Maintain diversity across these categories. **If your last 3 experiments were in the same category, switch.**

| Category | Examples |
|----------|----------|
| **Architecture** | Layer count, width, attention type, normalization, positional encoding, window patterns |
| **Optimization** | Optimizer params, LR schedules, warmup/cooldown, weight decay, gradient clipping |
| **Training dynamics** | Batch size, gradient accumulation, loss functions, sequence packing |
| **Simplification** | Removing components, reducing complexity for equal or better results |

### Experiment Ordering

1. **Early experiments**: Explore broadly. High-impact changes first (LR, depth, batch size).
2. **Middle experiments**: Tune what works. Combine successful ideas.
3. **When plateaued**: Structural leaps — new attention, new optimizer, architectural changes.

### Plateau Detection

Track the last 5 consecutive experiments. If **none** improved val_bpb by ≥0.001, you have hit a **plateau**.

When plateaued:
- **Stop tuning hyperparameters.** More LR sweeps will not help.
- **Make a structural change.** Different attention mechanism, normalization, positional encoding, optimizer algorithm.
- **Try something you haven't tried before.** Re-read train.py from scratch for new angles.
- **Combine previous near-misses.** Two ideas that each gave +0.0005 might give +0.002 together.

### What NOT to Do

- Don't change too many things at once — can't attribute improvements
- Don't abandon an idea after one failure — try 2-3 variations
- Don't over-optimize hyperparameters early — architecture changes invalidate them
- Don't compound untested changes
- A 0.0001 improvement is likely noise. Trust 0.001+ improvements.

---

## Phase 5: Memory & Logging

### results.tsv

Tab-separated, 5 columns. Append after every experiment:

```
commit	val_bpb	memory_gb	status	description
a1b2c3d	0.997900	44.0	keep	baseline
b2c3d4e	0.993200	44.2	keep	increase LR to 0.04
c3d4e5f	1.005000	44.0	discard	switch to GeLU activation
d4e5f6g	0.000000	0.0	crash	double model width (OOM)
```

Use `0.000000` for val_bpb and `0.0` for memory_gb on crashes.

### insights.md

Maintain alongside results.tsv. Update every ~5 experiments. **Overwrite stale entries** — this is compressed memory, not a log. Keep under ~50 lines.

Contents:
1. **Best result so far**: current best val_bpb, commit hash, configuration
2. **What works**: bullet list of changes that improved val_bpb
3. **What doesn't work**: bullet list of changes that hurt or had no effect
4. **Structural hypotheses**: 2-3 untried architectural ideas

**Always re-read insights.md before proposing each new experiment** to avoid re-exploring dead ends.

---

## Constraints — Hard Rules

These are inviolable:

1. **Only modify `train.py`**. Never touch `prepare.py`. Never touch `pyproject.toml`.
2. **No new dependencies.** Only what's already in pyproject.toml.
3. **The evaluation harness is sacred.** `evaluate_bpb` in prepare.py is ground truth. Never modify it.
4. **The time budget is fixed.** Training always runs for the same wall-clock duration. You optimize what happens within that budget.
5. **Never stop to ask.** The human may be asleep. Run autonomously until interrupted.
6. **Never commit results.tsv or insights.md.** They stay untracked.

---

## Autonomy Protocol

**NEVER STOP.** Once the experiment loop begins:

- Do NOT pause to ask the human if you should continue.
- Do NOT ask "should I keep going?" or "is this a good stopping point?"
- The human might be asleep. They expect you to work **indefinitely**.
- If you run out of ideas, think harder:
  - Re-read train.py from scratch
  - Re-read insights.md for unexplored hypotheses
  - Try combining previous successes
  - Try more radical architectural changes
  - Try reverting to baseline and taking a completely different path
  - Remove complexity instead of adding it

At ~5 minutes per experiment, you run ~12 experiments/hour. An overnight session produces ~100 experiments. The user wakes up to a full results.tsv and an optimized model.

---

## References

- [references/experiment-loop.md](references/experiment-loop.md) — Complete loop protocol with edge cases
- [references/strategy.md](references/strategy.md) — Deep strategy guide for experiment planning
- [references/architecture-ideas.md](references/architecture-ideas.md) — Catalog of architectural modifications to try
