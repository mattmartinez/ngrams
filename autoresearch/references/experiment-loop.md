# The Experiment Loop — Complete Protocol

This is the step-by-step protocol for each iteration of the experiment loop. Follow it exactly.

---

## Step 1: Plan the Experiment

Before touching any code:

1. **Read `insights.md`** (if it exists). Know what's been tried, what worked, what failed.
2. **Read `results.tsv`** tail. Check the last 5 experiments for plateau detection.
3. **Check experiment category diversity.** If the last 3 experiments were the same category (architecture/optimization/training-dynamics/simplification), pick a different one.
4. **Formulate a hypothesis.** Write it as: "I expect [change] will [improve/hurt] val_bpb because [reasoning]."
5. **Estimate risk.** Will this likely crash? OOM? Compile-time regression? Plan mitigation.

## Step 2: Edit train.py

Make your change. Follow these principles:

- **One change at a time.** Don't bundle unrelated modifications — you can't attribute improvements.
- **Keep it clean.** If you're adding complexity, it should be justified by expected gain.
- **Comment non-obvious changes.** A one-line comment explaining "why" helps future iterations.

### What you CAN change in train.py

Everything is fair game:
- Model architecture (depth, width, attention mechanism, normalization, activations)
- Optimizer configuration (LR, betas, warmup, cooldown, weight decay)
- Training dynamics (batch size, gradient accumulation)
- Model components (add/remove layers, change FFN ratio, modify attention)
- Hyperparameter values (all the constants at the top)
- The training loop structure itself

### What you CANNOT change

- `prepare.py` — read-only, contains the fixed evaluation and data pipeline
- `pyproject.toml` — no new dependencies
- The evaluation function `evaluate_bpb` — this is ground truth

## Step 3: Commit

```bash
git add train.py
git commit -m "experiment: <short description of what you changed>"
```

Good commit messages help track experiment history:
- `experiment: increase MATRIX_LR from 0.04 to 0.06`
- `experiment: add learned positional bias to attention`
- `experiment: remove value embeddings for simplification`
- `experiment: double DEPTH to 16 layers`

## Step 4: Run the Experiment

```bash
uv run train.py > run.log 2>&1
```

**Critical: redirect ALL output.** Never use `tee`. Never let training output flood your context window. The training script outputs progress lines with `\r` that would consume enormous context.

### Timeout handling

Each run should take ~5 minutes of training + a few seconds of startup and eval overhead. Total wall clock is typically 6-8 minutes.

If a run exceeds 10 minutes, kill it:
```bash
kill %1  # or kill the process directly
```
Log it as a timeout/crash and move on.

### Running in background

Use `bg_shell` to run the experiment without blocking:
```
bg_shell start: uv run train.py > run.log 2>&1
```
Then wait for completion or poll with `digest`.

## Step 5: Read Results

**Primary method — structured JSON:**
```bash
cat results.json
```

This gives: `{"val_bpb": 0.997, "training_seconds": 300.1, "peak_vram_mb": 45060.2, ...}`

**Fallback — grep from log:**
```bash
grep "^val_bpb:\|^peak_vram_mb:" run.log
```

**Crash detection:** If neither results.json exists nor grep produces output:
```bash
tail -n 50 run.log
```
Read the Python traceback. Determine if it's fixable.

## Step 6: Handle Crashes

If the run crashed:

1. **Read the traceback**: `tail -n 50 run.log`
2. **Classify the failure:**
   - **Trivial bug** (typo, missing import, wrong variable name): Fix it, amend the commit, re-run.
   - **OOM**: The model/batch is too large. Log as crash, revert, try smaller.
   - **Fundamentally broken idea**: Log as crash, revert, move on.
3. **Don't spend more than 2-3 attempts fixing a single crash.** If it's not working, it's not worth it.

## Step 7: Log Results

Append a row to `results.tsv` (tab-separated):

```bash
# Extract values
COMMIT=$(git rev-parse --short HEAD)
VAL_BPB=$(python3 -c "import json; print(json.load(open('results.json'))['val_bpb'])" 2>/dev/null || echo "0.000000")
MEMORY_GB=$(python3 -c "import json; print(f\"{json.load(open('results.json'))['peak_vram_mb']/1024:.1f}\")" 2>/dev/null || echo "0.0")
```

Or just read results.json and write the row directly. The format:

```
<commit>\t<val_bpb>\t<memory_gb>\t<status>\t<description>
```

## Step 8: Keep or Discard

Compare the new val_bpb against the **current best** (the val_bpb on the commit you started from).

**If improved (keep):**
- Leave the commit in place. The branch has advanced.
- Log status as `keep`.

**If not improved (discard):**
- Revert: `git reset --hard HEAD~1`
- Log status as `discard`.
- The branch is back where it was. Try something else.

**If crashed:**
- If you already fixed and re-ran successfully, evaluate normally.
- If giving up: `git reset --hard HEAD~1`, log status as `crash`.

## Step 9: Update insights.md

Every ~5 experiments, update `insights.md`. This is your compressed memory.

```markdown
# Insights

## Best Result
- val_bpb: 0.9832 (commit abc1234)
- Config: DEPTH=10, MATRIX_LR=0.06, TOTAL_BATCH_SIZE=524288

## What Works
- Increasing MATRIX_LR from 0.04 to 0.06 (-0.005 val_bpb)
- Increasing DEPTH from 8 to 10 (-0.008 val_bpb)
- Reducing WARMDOWN_RATIO to 0.3 (-0.002 val_bpb)

## What Doesn't Work
- GeLU activation (worse by 0.008)
- Disabling sliding window (no improvement, slower)
- Weight decay > 0.3 (training instability)

## Untried Hypotheses
- Multi-query attention (n_kv_head < n_head)
- Linear attention variants
- Post-norm instead of pre-norm
```

**Keep under ~50 lines.** Overwrite stale entries. This is not a log — it's living memory.

## Step 10: Loop

Return to Step 1. **Do not stop.**

---

## Edge Cases

### The very first run

The first experiment is always the **baseline**. Run train.py as-is, unmodified. Record this as the baseline in results.tsv with status `keep`. All future experiments are compared against the current best.

### Reverting past a successful experiment

Very rarely, you might want to revert to an earlier state and try a different path. This is valid but should be extremely rare. Use:
```bash
git log --oneline -20  # find the commit you want
git reset --hard <commit>
```
Update insights.md to note the rewind.

### Multiple consecutive crashes

If 3+ experiments crash in a row, something systemic is wrong. Steps:
1. Revert to the last known-good commit
2. Re-read train.py completely
3. Try a minimal, conservative change
4. If crashes persist, check for environment issues (GPU, CUDA, disk space)

### Running out of ideas

This should never happen, but if it feels like it:
1. Re-read `train.py` from the very top, line by line
2. Re-read `prepare.py` for the evaluation mechanics
3. Re-read `insights.md` — combine two "what works" items
4. Try **removing** something instead of adding
5. Try a completely different architectural paradigm
6. Look at what parameters you've never touched
7. Try extreme values (2x or 0.5x) of something usually tweaked incrementally
