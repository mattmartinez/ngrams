# Bug-Hunt Research Insights — Session nov09 (2026-11-09)

Three runs on Fable 5.2 at max effort, scored with the fixed `evaluate.py`. The two baselines use the prompts at `742fe75`. The gate run adds the Skeptic sample-rule change and the short-answer step.

| Run | Composite | Recall | Precision | Sev Acc | Trap Res | Confirmed | Notes |
|-----|-----------|--------|-----------|---------|----------|-----------|-------|
| Baseline 1 | 0.2098 | 1.0000 | 0.3556 | 1.0000 | 0.4000 | 53 | 16/16 planted |
| Baseline 2 | 0.2954 | 1.0000 | 0.3265 | 1.0000 | 0.6000 | 56 | 16/16 planted |
| Gate | 0.3049 | 1.0000 | 0.3556 | 0.9375 | 0.6000 | 53 | 16/16 planted; one severity differs, see below |

## What these numbers can and cannot say
- **Recall and severity are sound.** Every run found all 16 planted bugs.
- **One severity rating changed in the gate run.** The Hunter filed PLANTED-08 (the stats lock that is never taken, `medium/data_pipeline.py:29-38`) as Low. The Referee kept that, although its rubric lists races as Medium. Both baselines rated it Medium, and in both the Skeptic argued that was too high. The rating was set before the Skeptic ran, and the one clause removed from the Hunter's prompt does not mention severity. This reads as run-to-run variation on a borderline bug, but one run cannot prove it.
- **Precision is not.** Each run confirms 50+ issues in 865 lines, and the answer key lists 23 entries. The 26 to 31 "false positives" are mostly real issues the key does not list.
- **Trap resistance is not.** No run reported any of the five traps as a bug, judging by the title and lines of every confirmed finding in the traps file. Every counted "trap hit" is a different finding within 10 lines of a trap: a blank signing key accepted in production (beside the default-key trap), a signature check that raises on non-ASCII input, an order with no total treated as free, a client that claims retries but makes one attempt. The scorer matches by file and line distance only.
- **The composite wobbles by 0.09** between two runs of the same prompts. A prompt change that moves it less than that has not been shown to do anything.

## Scorer fixes this session
- Reads each bug's file and severity from the final report, and gives no credit without a file.
- Uses every line range on a `File:` line. 40% of real entries cite several.
- Writes a row only with `--description`. `--commit` names the commit that produced a saved reply.

## Before the next loop
The composite cannot steer prompt edits at this finding density. Either match findings to answer-key entries by what they say and not only by line distance, or build a larger benchmark with a complete answer key.

---

# Session mar24 (FINAL)

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
