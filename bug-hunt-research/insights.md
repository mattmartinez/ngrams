# Bug-Hunt Research Insights — Session mar24 (FINAL)

## Results Summary

| Run | Composite | F1 | Recall | Precision | Sev Acc | Trap Res | Notes |
|-----|-----------|-----|--------|-----------|---------|----------|-------|
| Baseline | 0.6875 | 0.9375 | 0.9375 | 0.9375 | 0.7333 | 1.0000 | Unmodified prompts |
| Exp 1 | 0.7097 | 0.9032 | 0.8750 | 0.9333 | 0.7857 | 1.0000 | Severity examples in hunter |
| Exp 2 | 0.7500 | 0.9375 | 0.9375 | 0.9375 | 0.8000 | 1.0000 | Severity in referee |
| Exp 3 | **1.0000** | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | Fix token/PRNG severity + Low format |
| Validation | 0.9375 | 0.9375 | 0.9375 | 0.9375 | 1.0000 | 1.0000 | Stochastic miss |
| Exp 4 | 0.9677 | 0.9677 | 0.9375 | 1.0000 | 1.0000 | 1.0000 | Auth line guidance |
| Exp 5 | **1.0000** | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | Unicode + unused imports |
| Val 2 | **1.0000** | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | Consistency check |
| Val 3 | **1.0000** | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | Consistency check |

**Improvement: 0.6875 → 1.0000 (+45.5%)**

## Key Changes (all kept)

### Hunter (prompts/hunter.md)
1. Detailed severity ✅/❌ examples with concrete bug types
2. Token truncation + predictable PRNG → Critical
3. Off-by-one → Low
4. "When in doubt pick lower severity"
5. Auth line-number guidance (include definition + usage)
6. Unicode confusable + unused import checklist items
7. Anti-false-positive guidance

### Referee (prompts/referee.md)
1. Severity calibration matching hunter's categories
2. "Preserve hunter severity unless clearly wrong"
3. `### BUG-N` structured format for ALL severities (not tables)

### Skeptic (prompts/skeptic.md)
No changes — performed well at baseline.

## Key Insight
The single biggest lever was **severity calibration alignment**. The baseline found 15/16 bugs with 0/5 traps triggered — excellent recall and trap resistance. But severity accuracy was only 73%, crushing the composite score. Aligning severity definitions between hunter, referee, and ground truth (with concrete examples) pushed severity accuracy from 73% → 100%, which drove composite from 0.6875 → 1.0.
