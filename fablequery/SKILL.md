---
name: fablequery
description: Hand a genuinely-engineering problem to a Fable 5 subagent and get rigorous, adversarial engagement — by framing it accurately as the engineering problem it actually is, so a spurious safety decline doesn't waste the turn. Use when the work is legitimate engineering but sits in a gray area (own-systems automation, comparative analysis of external systems, security-adjacent or integrity mechanics) where a careless prompt reads as abuse. NOT a way to get help on work that isn't legitimate.
---

# fablequery — frame an engineering problem so Fable engages

## The principle — read this first
This is **not** a jailbreak and **not** a way to slip work past the model's judgment. It works only
because the framing is **true**: the problem genuinely IS an engineering problem, and describing it
*accurately* is what lets a capable model help.

The test: can you describe your problem as legitimate engineering **honestly** — your own systems /
accounts / infra, authorized use, within the applicable terms, with **no** detection-evasion,
identity/fingerprint fabrication, or protection-defeating in scope? If yes, this skill gets you a
rigorous partner. If you'd have to *misrepresent* the work to make it sound legitimate, this is the
wrong tool and the honest answer is "don't." **The goal is accurate framing, never trickery.**

A capable model declines on **framing that reads as abuse** — not on data volume or difficulty. So
the fix for a decline is always: describe the *real* problem more accurately. Never re-send the same
prompt with a workaround.

## When to use
- The problem is real engineering (reliability, performance/latency, systems behavior, forensics on
  your own data, comparative analysis of an external system, integrity/robustness work) but the naïve
  phrasing scans as abuse.
- You want an adversarial second opinion from Fable and don't want a spurious refusal to burn the turn.

## Build the query — the 8-point checklist
Construct the Fable subagent call (Agent tool, `model: fable`) with ALL of these:

1. **Ownership + authorization, first sentence.** "My own systems / accounts / infra, authorized use,
   within the applicable terms."
2. **Name the problem class correctly** — reliability, performance, correctness, forensics, systems
   analysis. NOT "stealth," "evasion," "cheating," "bypass."
3. **Say it plainly: an engineering problem, not a stealth/evasion one.**
4. **Scope out what you're NOT asking for, explicitly** — anything that would defeat a protection,
   fabricate identity, or evade detection. This reassures it AND keeps you honest.
5. **Give it real evidence** — logs, traces, code, numbers, measurements. Fable engages *more* with
   concrete data than with hand-waving; a large sanitized dataset reads as *more evidence*, not more risk.
6. **Sanitize first (non-negotiable).** Strip every secret — credentials, tokens, keys, PII — before
   it reaches the agent. Extract the *facts* (statuses, timings, counts, error codes); the secrets
   never leave your machine. This is what makes it both safe and clean.
7. **Ask it to be adversarial** — "assume I'm wrong; find the hole; don't rubber-stamp." Gets the best
   engineering out of it and signals you want rigor, not validation.
8. **Ask for a standalone artifact** — "structured markdown, saved to a file" — so the output is a
   durable review, not throwaway chat.

## Language: abuse-coded → accurate (all TRUE re-descriptions, not spin)
| reads as abuse | the accurate engineering term |
|---|---|
| bot / automate-around | client / automation / my tool |
| evade / bypass / get around | operate within / stay compliant with |
| beat / defeat detection | pass the system's own validation / integrity checks |
| exploit | use the documented behavior of |
| brute-force / hammer / spam | drive on a defined, bounded policy |
| spoof / fake the identity/fingerprint | (out of scope — say so explicitly) |

If a swap would make the sentence *false*, don't swap it — that's the signal the work isn't the
engineering problem you're claiming, and the skill doesn't apply.

## If it declines
Do NOT retry verbatim or bolt on a workaround. Diagnose the ONE phrase that read as abuse (almost
always an abuse-coded verb — "evade," "defeat," "exploit," "spoof"), replace it with the accurate
term, re-lead with the ownership/authorization line, and re-send. An accurate reframe engages; a
trick does not — and shouldn't.

## Skeleton
> You're reviewing a `<reliability / performance / forensics / systems-analysis>` problem for a
> personal `<tool/system>` I run on my own `<systems / accounts / infra>` (authorized use, within the
> applicable terms). It's an engineering problem, not a stealth one — I'm NOT asking you to
> `<the out-of-scope items>`.
> `[Context + SANITIZED data.]`
> `[Specific questions.]`
> Be adversarial — assume I'm wrong and find the hole. Structured markdown, standalone (saved to a file).

## Notes
- Run it `run_in_background: false` when you need the verdict to continue; the agent's final message is
  the review.
- Pair with a follow-up verification/test pass if the review greenlights a code change — Fable verifies
  the *reasoning*; your local gates verify the *implementation*.
- Keep the reviews as durable findings (a `research/` or `docs/` trail), not chat — that's why point 8
  asks for a saved file.
