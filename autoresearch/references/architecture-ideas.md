# Architecture Ideas Catalog

A reference catalog of architectural modifications to try when experimenting. Each entry includes the change, expected impact, risk level, and implementation notes.

Use this as a menu when planning experiments — especially when you've plateaued on hyperparameter tuning.

---

## Attention Variants

### Grouped-Query Attention (GQA)
**What:** Use fewer KV heads than query heads (e.g. n_kv_head = n_head / 2 or / 4).
**Expected impact:** Reduces memory and compute per attention layer. May slightly hurt quality but allows more layers in the same time budget.
**Risk:** Low — well-established technique (Llama 2, Mistral).
**Implementation:** Change `n_kv_head` in build_model_config. The attention code already supports n_kv_head ≠ n_head via `fa3.flash_attn_func`.

### Multi-Head Latent Attention (MLA)
**What:** Compress KV projections through a low-rank bottleneck before expanding to per-head KV.
**Expected impact:** Significant memory savings with minimal quality loss at small scales.
**Risk:** Medium — requires new code, may not help at this model scale.
**Implementation:** Add a low-rank projection layer before K and V computation in CausalSelfAttention.

### Different Window Patterns
**What:** Change the ratio and arrangement of sliding-window vs full-attention layers.
**Expected impact:** More sliding window = faster steps. More full attention = better long-range modeling.
**Risk:** Low.
**Implementation:** Change `WINDOW_PATTERN` string (e.g. "SSSSL", "SL", "SSSSSSSL").

### Linear Attention
**What:** Replace softmax attention with a linear approximation (e.g. ReLU-based, cosine similarity).
**Expected impact:** O(n) instead of O(n²) — much faster for long sequences. Usually hurts quality.
**Risk:** High — significant quality degradation likely.
**Implementation:** Would need to bypass FA3 and implement custom attention.

---

## FFN Variants

### SwiGLU / Gated Linear Units
**What:** Replace `relu().square()` activation with a gated mechanism: `SiLU(xW₁) ⊙ xW₂`.
**Expected impact:** Often improves quality. Standard in modern LLMs (Llama, Mistral).
**Risk:** Low-medium — adds a third weight matrix to each FFN, increasing params.
**Implementation:** Add `c_gate` linear layer to MLP. Change expansion to `(8/3)*n_embd` to keep param count similar. Forward: `self.c_proj(F.silu(self.c_gate(x)) * self.c_fc(x))`.

### Larger/Smaller FFN Ratio
**What:** Change the 4x expansion ratio in FFN.
**Expected impact:** Larger = more capacity per layer. Smaller = more layers in same budget.
**Risk:** Low.
**Implementation:** Change `4 * config.n_embd` to `N * config.n_embd` in MLP.

### Mixture of Experts (MoE)
**What:** Replace single FFN with multiple expert FFNs, route tokens to top-k experts.
**Expected impact:** Dramatically more parameters with similar compute. Often big quality gains.
**Risk:** High — complex implementation, routing instability, load balancing.
**Implementation:** Multiple MLP instances per layer + routing network.

---

## Normalization Variants

### Post-Norm (instead of Pre-Norm)
**What:** Apply normalization after the residual add instead of before the sublayer.
**Expected impact:** Can help training stability in some configurations.
**Risk:** Medium — may require LR re-tuning.
**Implementation:** Change Block.forward to normalize after addition.

### Sandwich Norm
**What:** Apply norm both before and after each sublayer.
**Expected impact:** Better training stability, especially at larger scales.
**Risk:** Low — adds minimal compute.
**Implementation:** Add a second norm call in Block.forward after attention/MLP output, before residual add.

### QK Norm Removal
**What:** The model currently normalizes Q and K before attention. Remove this.
**Expected impact:** Might slightly improve quality if QK norm was hurting expressiveness. Might hurt stability.
**Risk:** Medium — could cause training instability.
**Implementation:** Remove `q, k = norm(q), norm(k)` from CausalSelfAttention.forward.

---

## Positional Encoding Variants

### Different RoPE Base Frequency
**What:** Change the base from 10000 to something else (e.g. 100000 for NTK-aware).
**Expected impact:** Higher base = better extrapolation to longer sequences. May slightly affect quality at training length.
**Risk:** Low.
**Implementation:** Change `base=10000` in `_precompute_rotary_embeddings`.

### ALiBi (Attention with Linear Biases)
**What:** Replace RoPE with linear attention biases that penalize distant tokens.
**Expected impact:** No positional parameters needed. Good length extrapolation.
**Risk:** Medium — requires modifying the attention kernel call (may not be supported by FA3).
**Implementation:** Would need to check FA3 support for attention bias.

### No Positional Encoding
**What:** Remove RoPE entirely and rely on causal mask + learned patterns.
**Expected impact:** Unlikely to work well for autoregressive LM, but worth trying as a baseline.
**Risk:** High — probably hurts significantly.
**Implementation:** Remove cos/sin from attention, remove apply_rotary_emb calls.

---

## Training Dynamics

### Logit Soft-Cap Tuning
**What:** Change the soft-cap value from 15.0 to something else (e.g. 30, 50, or remove).
**Expected impact:** Lower cap = more regularization. Higher/no cap = more expressive.
**Risk:** Low.
**Implementation:** Change `softcap = 15.0` in GPT.forward.

### Label Smoothing
**What:** Add label smoothing to cross_entropy loss.
**Expected impact:** Slight regularization, can prevent overconfident predictions.
**Risk:** Low.
**Implementation:** Add `label_smoothing=0.1` to `F.cross_entropy()`.

### Z-Loss
**What:** Add a penalty on the log-partition function (logsumexp of logits).
**Expected impact:** Prevents logit drift, stabilizes training.
**Risk:** Low.
**Implementation:** Add `z_loss = 1e-4 * logsumexp(logits, dim=-1).square().mean()` to the loss.

### Gradient Clipping
**What:** Clip gradient norm before optimizer step.
**Expected impact:** Prevents training instability from gradient spikes.
**Risk:** Low — nearly always safe to add.
**Implementation:** `torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)` before `optimizer.step()`.

---

## Simplification Targets

These are components to try **removing**. Equal or better val_bpb with less code is always a win.

| Component | Param cost | Try removing? |
|-----------|-----------|---------------|
| Value embeddings | Significant (vocab × kv_dim × num_ve_layers) | Yes — big simplification if val_bpb holds |
| x0_lambdas (input residual) | Tiny (n_layer scalars) but adds code | Yes — check if input residual actually helps |
| resid_lambdas | Tiny but adds code | Yes — may be unnecessary |
| QK normalization | Zero params, minimal compute | Maybe — could help or hurt |
| Logit soft-capping | Zero params | Maybe — try removing |
| Sliding window | Zero params, changes attention pattern | Try all-full or all-sliding |
| Muon optimizer | Code complexity | Try pure AdamW for everything |
| Weight decay schedule | Code complexity | Try constant weight decay |
| Momentum warmup | Code complexity | Try constant momentum |
