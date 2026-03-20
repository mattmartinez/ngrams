# Experiment Strategy Guide

Deep strategy for planning experiments, avoiding pitfalls, and escaping plateaus.

---

## High-Impact Changes (Try First)

These categories historically produce the largest improvements. Start your session with these before fine-tuning.

### 1. Learning Rates

The most impactful single change. The model uses multiple LR groups:

| Parameter | Controls | Default | Range to explore |
|-----------|----------|---------|-----------------|
| `MATRIX_LR` | Transformer weight matrices (Muon optimizer) | 0.04 | 0.01 – 0.10 |
| `EMBEDDING_LR` | Token embeddings + value embeddings (Adam) | 0.6 | 0.1 – 1.0 |
| `UNEMBEDDING_LR` | lm_head output projection (Adam) | 0.004 | 0.001 – 0.01 |
| `SCALAR_LR` | Per-layer residual/x0 scalars (Adam) | 0.5 | 0.1 – 1.0 |

Tips:
- MATRIX_LR has the most impact — it controls the bulk of parameters.
- Often 2-4x improvements possible from LR tuning alone.
- The LRs are **already scaled** by `1/√(dmodel/768)`. Changing DEPTH changes dmodel which changes effective LR.

### 2. Model Size (DEPTH)

`DEPTH` controls the number of transformer layers. Model dimension is `DEPTH * ASPECT_RATIO`.

- More depth = more capacity, but fewer steps in fixed time budget
- Fewer depth = faster steps, but less capacity
- There's an optimal depth that maximizes val_bpb for the given time budget and GPU
- Try: 6, 8, 10, 12, 16

The interplay between depth and LR is critical — when you change depth, the optimal LR changes too.

### 3. Batch Size

`TOTAL_BATCH_SIZE` is tokens per optimizer step. Default is `2**19` (~524K).

- Larger batch = fewer steps but more stable gradients
- Smaller batch = more steps but noisier gradients
- Effective range: `2**17` to `2**21`
- `DEVICE_BATCH_SIZE` is per-GPU microbatch (affects VRAM). `TOTAL_BATCH_SIZE / (DEVICE_BATCH_SIZE * MAX_SEQ_LEN)` = gradient accumulation steps
- Must be divisible: `TOTAL_BATCH_SIZE % (DEVICE_BATCH_SIZE * MAX_SEQ_LEN) == 0`

---

## Experiment Categories

### Architecture

Changes to the model structure itself.

**Attention:**
- Number of KV heads (`n_kv_head`) — try grouped-query attention (n_kv_head < n_head)
- Head dimension (`HEAD_DIM`) — 64 vs 128 vs 256
- Window pattern (`WINDOW_PATTERN`) — ratio of sliding vs full attention layers
- Sliding window size — currently half of sequence length

**FFN:**
- FFN expansion ratio — currently 4x. Try 3x or 8/3x (SwiGLU-style)
- Activation function — currently `relu().square()`. Try SiLU, GELU, swish
- Gated FFN (SwiGLU) — adds a gate projection

**Normalization:**
- Currently uses RMSNorm (`F.rms_norm`). Could try LayerNorm, or different norm placement
- Pre-norm vs post-norm vs sandwich norm

**Positional encoding:**
- Currently RoPE. Could try ALiBi, learned position embeddings, NoPE
- RoPE base frequency (currently 10000)

**Residual connections:**
- Has per-layer `resid_lambdas` and `x0_lambdas` (input residual)
- Try different initialization or schedules for these
- Try removing x0_lambdas (simplification)

**Value embeddings:**
- Currently alternating layers have value embeddings with input-dependent gates
- Try: all layers, no layers, different gating mechanisms

### Optimization

Changes to how the model is trained.

**Optimizer:**
- MuonAdamW: Muon for 2D matrices, AdamW for the rest
- Muon parameters: momentum, ns_steps (Newton-Schulz iterations), beta2
- Adam betas, epsilon
- Try: different momentum schedules, warmup for momentum

**LR schedule:**
- Currently: linear warmup → constant → cosine cooldown
- `WARMUP_RATIO` (default 0.05) — fraction of budget for warmup
- `WARMDOWN_RATIO` (default 0.5) — fraction of budget for cooldown
- `FINAL_LR_FRAC` (default 0.0) — final LR as fraction of peak
- Try: different warmup/cooldown ratios, WSD schedule, no warmdown

**Weight decay:**
- Currently 0.2, decays linearly to 0 over training
- Applied only to Muon params (cautious weight decay with mask)
- Try: constant decay, higher/lower values, apply to Adam params too

**Gradient clipping:**
- Not currently used. Could add global or per-group clipping.

### Training Dynamics

**Batch size:** See above.

**Gradient accumulation:** Derived from TOTAL_BATCH_SIZE / DEVICE_BATCH_SIZE. Changing DEVICE_BATCH_SIZE changes memory vs accumulation tradeoff.

**Loss function:**
- Currently cross_entropy with logit soft-capping at 15.0
- Try: different soft-cap values, z-loss regularization, label smoothing

**Compilation:**
- Model is `torch.compile`'d with `dynamic=False`
- The first ~10 steps are warmup (excluded from time budget)
- Could experiment with compile options or partial compilation

### Simplification

**The most underrated category.** Removing complexity that doesn't help is a valid improvement.

Things to try removing:
- Value embeddings (significant parameter cost)
- Per-layer lambda scalars (resid_lambdas, x0_lambdas)
- Sliding window attention (just use full attention)
- QK normalization
- Logit soft-capping
- The Muon optimizer complexity (just use AdamW for everything)

If removing something gives equal val_bpb, that's a **win** — keep the simpler version.

---

## Plateau Escape Strategies

When the last 5 experiments all failed to improve by ≥0.001:

### Strategy 1: Structural leap
Change something fundamental about the architecture:
- Different attention mechanism entirely
- Add or remove an entire component (FFN, normalization, residual path)
- Change the optimizer algorithm

### Strategy 2: Scale shift
Move to a very different model size:
- If currently at DEPTH=8, try DEPTH=16 or DEPTH=4
- If currently at batch_size=2^19, try 2^17 or 2^21

### Strategy 3: Combination attack
Combine 2-3 previously successful changes that were tested independently:
- If +LR helped and +depth helped, try both together
- Interactions between changes can produce super-additive gains

### Strategy 4: Fresh start
Revert to baseline and take a completely different path:
- Start from the original train.py
- Apply only the single most impactful change you've found
- Explore in a different direction from there

### Strategy 5: Inversion
Try the **opposite** of what seems right:
- If you've been increasing LR, try halving it
- If you've been adding layers, try removing them
- If you've been adding components, try stripping to minimal

---

## Pitfall Catalog

| Pitfall | Why it's bad | How to avoid |
|---------|-------------|-------------|
| Changing 3+ things at once | Can't attribute improvements | One change per experiment |
| Abandoning after one failure | Idea might work with different params | Try 2-3 variations |
| Only tuning hyperparameters | Diminishing returns quickly | Alternate with architecture changes |
| Trusting 0.0001 improvements | That's noise | Only trust ≥0.001 |
| Over-optimizing LR for current architecture | Architecture change invalidates LR | Re-tune LR after architecture changes |
| Never trying simplification | Missing easy wins | Every 5th experiment should simplify |
| Ignoring VRAM growth | Eventually OOMs | Track memory, flag >20% growth |
| Not reading insights.md | Re-exploring dead ends | Always read before planning |
