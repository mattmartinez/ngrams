# Hyperparameter Ranges

Validated exploration ranges for the tunable hyperparameters at the top of `train.py`. For each entry: **default** (current value in train.py), **min/max** (bounds within which the run still trains stably on a single H100/A100 with the existing time budget), **interactions** (other hyperparameters whose tuning is invalidated by changing this one), and **re-baseline** (whether changing it forces re-tuning baselines from `insights.md`).

Numbers without bounds are not "do not touch" — they are "you are off the validated map." Take a baseline first.

## DEPTH

- Default: `8`
- Min/Max: `4` / `16`
- Interactions: `ASPECT_RATIO` (model_dim is derived as `depth * ASPECT_RATIO`), `MATRIX_LR`, `EMBEDDING_LR`, `WARMUP_RATIO`. Changing depth shifts model_dim, which scales LRs by `1/sqrt(model_dim/768)` automatically — but the *relative* ordering between LRs may need re-tuning.
- Re-baseline: **Yes.** Depth is the dominant scaling axis; every other "what works" insight is conditioned on it.

## ASPECT_RATIO

- Default: `64`
- Min/Max: `48` / `96`
- Interactions: `DEPTH` (controls model_dim together), `HEAD_DIM` (model_dim is rounded up to a multiple of HEAD_DIM, so num_heads changes), all LRs (LR scaling is dmodel-dependent).
- Re-baseline: **Yes** if changing by more than ±16. Small tweaks within ±8 usually carry forward.

## HEAD_DIM

- Default: `128`
- Min/Max: `64` / `128`
- Interactions: `ASPECT_RATIO` (num_heads = model_dim // HEAD_DIM, so smaller HEAD_DIM means more heads at the same model_dim), attention compute cost, FA3 kernel performance (FA3 is well-tuned at 64/128).
- Re-baseline: **Yes.** Different num_heads changes the attention pattern's expressiveness and the rotary precomputation.

## WINDOW_PATTERN

- Default: `"SSSL"`
- Min/Max: any non-empty string of `S` and `L` characters (the last layer is always forced to long context internally). Validated patterns: `"SSSL"`, `"SSLL"`, `"SLSL"`, `"LLLL"`, `"SSSS"`.
- Interactions: `MAX_SEQ_LEN` (S = half of long_window, L = full), per-step compute cost, MFU.
- Re-baseline: **No** for cosmetic shuffles within the same S/L count; **yes** if the count of L layers changes.

## TOTAL_BATCH_SIZE

- Default: `2**19` (524288 tokens/step)
- Min/Max: `2**17` (131072) / `2**21` (2097152). Must be divisible by `DEVICE_BATCH_SIZE * MAX_SEQ_LEN`.
- Interactions: All LRs (large-batch scaling), `WARMUP_RATIO` (more warmup needed at larger batches), `WEIGHT_DECAY`, `grad_accum_steps` (derived).
- Re-baseline: **Yes.** Optimizer dynamics change materially with batch size.

## DEVICE_BATCH_SIZE

- Default: `128` (overridable via env var)
- Min/Max: `16` / `128`. Valid values are powers of two that divide `TOTAL_BATCH_SIZE / MAX_SEQ_LEN` (256 at defaults) — the validated set is `16`/`32`/`64`/`128`. (`256` divides but OOMs on validated GPUs at depth 8, so `128` is the practical max.) For OOM recovery, halve `DEVICE_BATCH_SIZE` (e.g. 128 → 64).
- Interactions: `TOTAL_BATCH_SIZE` (must divide evenly), `grad_accum_steps`, `peak_vram_mb`. Does *not* change training dynamics — only memory layout and step time.
- Re-baseline: **No.** Changing only DEVICE_BATCH_SIZE (with TOTAL_BATCH_SIZE held constant) is mathematically a no-op for the optimizer — the only effect is on speed and memory.

## EMBEDDING_LR

- Default: `0.6`
- Min/Max: `0.05` / `1.5`
- Interactions: `MATRIX_LR` (the embedding/matrix LR ratio is the load-bearing knob — see Dangerous Combinations), `ADAM_BETAS`, `WARMUP_RATIO`. Embedding LR is auto-scaled by `1/sqrt(model_dim/768)`.
- Re-baseline: **No** for ±2x sweeps that match prior tuning intuition; **yes** if you cross an order of magnitude.

## UNEMBEDDING_LR

- Default: `0.004`
- Min/Max: `0.0005` / `0.02`
- Interactions: `EMBEDDING_LR` (lm_head and wte are tied conceptually but trained separately here), `ADAM_BETAS`, output softcap of 15.
- Re-baseline: **No** for ±5x; **yes** otherwise.

## MATRIX_LR

- Default: `0.04`
- Min/Max: `0.005` / `0.15`
- Interactions: `WEIGHT_DECAY` (Muon weight decay decays linearly with progress), `EMBEDDING_LR` ratio, `WARMUP_RATIO`, `WARMDOWN_RATIO`. Muon LR is scaled by `max(1, shape[-2]/shape[-1])**0.5` per matrix shape.
- Re-baseline: **Yes.** This is the most sensitive optimization knob — changes alter loss curvature significantly.

## SCALAR_LR

- Default: `0.5`
- Min/Max: `0.05` / `2.0`
- Interactions: `resid_lambdas` and `x0_lambdas` updates. The resid group uses `SCALAR_LR * 0.01`, the x0 group uses `SCALAR_LR` directly with custom betas.
- Re-baseline: **No.** Scalar params are a tiny fraction of total parameters; changes here rarely move val_bpb meaningfully.

## WEIGHT_DECAY

- Default: `0.2`
- Min/Max: `0.0` / `0.5`
- Interactions: `MATRIX_LR` (cautious WD multiplies LR), training time (WD decays linearly with progress to 0). Higher WD demands longer warmup.
- Re-baseline: **No** for sweeps within ±0.1; **yes** for >0.3 changes.

## ADAM_BETAS

- Default: `(0.8, 0.95)`
- Min/Max: `beta1` ∈ `[0.5, 0.95]`, `beta2` ∈ `[0.9, 0.999]`. The `x0_lambdas` group overrides to `(0.96, 0.95)`.
- Interactions: All Adam-group LRs (embeddings, lm_head, scalars), `WARMUP_RATIO` (lower beta1 needs less warmup).
- Re-baseline: **Yes** if either beta moves by >0.1.

## WARMUP_RATIO

- Default: `0.05`
- Min/Max: `0.01` / `0.20`
- Interactions: All LRs (effective LR area-under-curve changes), `TOTAL_BATCH_SIZE` (larger batches want more warmup), `MATRIX_LR` (Muon is more sensitive to warmup than Adam).
- Re-baseline: **No** for ±0.05; **yes** for larger swings.

## WARMDOWN_RATIO

- Default: `0.5`
- Min/Max: `0.1` / `0.8`. Plus `WARMUP_RATIO` must stay below `1 - WARMDOWN_RATIO` or there is no flat plateau.
- Interactions: `FINAL_LR_FRAC` (controls the floor), all LRs (cooldown integral). Long warmdown effectively reduces total LR area.
- Re-baseline: **No** for ±0.2; **yes** otherwise.

## FINAL_LR_FRAC

- Default: `0.0`
- Min/Max: `0.0` / `0.2`
- Interactions: `WARMDOWN_RATIO` (controls how fast we descend to this floor), `MATRIX_LR` (a non-zero floor matters most for Muon).
- Re-baseline: **No.** Tail-of-training tweak; impact on val_bpb is small relative to other knobs.

## Dangerous Combinations

These combinations are known to fail or invalidate prior baselines. Do not change both halves of a pair in the same experiment.

- **`DEPTH` and `MATRIX_LR` together.** Changing depth re-scales model_dim, which auto-scales LRs by `1/sqrt(model_dim/768)`. If you also bump `MATRIX_LR` manually, you will not be able to attribute whether a result came from the geometry change or the optimizer change. **Expected failure mode:** silently misleading "improvements" that are actually just LR retuning, and a destabilized loss curve that may NaN within the first 1000 steps.
- **`DEVICE_BATCH_SIZE` increase + `WINDOW_PATTERN` swap from `S` to `L`.** Both moves grow the attention activation footprint. Doing both simultaneously is the canonical OOM. **Expected failure mode:** CUDA OOM during the first forward pass or during the FA3 backward. Recovery: revert both and apply only one.
- **`EMBEDDING_LR` >= 1.0 with `WARMUP_RATIO` <= 0.02.** High embedding LR without enough warmup steps lets the embedding table take giant early steps before the rest of the network can keep up. **Expected failure mode:** loss spikes > 100 inside the first ~50 steps, triggering the in-loop NaN/exploding-loss guard at lines ~604-607 and exiting non-zero.
- **`TOTAL_BATCH_SIZE` doubled + `MATRIX_LR` held constant.** Larger batches without compensating LR (and warmup) lead to under-trained models within the fixed time budget — fewer optimizer steps, no LR scaling to absorb the change. **Expected failure mode:** val_bpb stays flat or regresses by 0.01-0.03 versus baseline, easily mistaken for "this idea didn't work" when it's really an under-tuned schedule.
- **`DEPTH` + `ASPECT_RATIO` both increased.** Both grow model_dim multiplicatively, easily landing you outside the validated VRAM/compute envelope. **Expected failure mode:** OOM or sub-baseline MFU because the time budget is consumed by fewer, larger steps.
