---
name: fablequery
description: Hand an engineering problem to a Fable subagent for a well-scoped adversarial review backed by evidence. The prompt says plainly what the system is, who owns it and what the work does, carries sanitized data, and asks for a saved review. Use for a rigorous second opinion on reliability, performance, forensics, security or integrity work on systems you own or are authorized to test. NOT a way to get help on work that isn't legitimate.
---

# fablequery — a well-scoped adversarial review from Fable

## The principle — read this first
The subagent decides for itself whether to help. Your job is to give it what it needs to decide well:
what the system is, who owns each side of it, what the work does, and real evidence. Nothing else.

- **Use the plain word when it is the true one.** "I'm testing my own rate limiter by driving it past
  its limit" is honest and useful. Do not swap a word for a softer one to change how the prompt reads.
- **State only what you know.** Never assert ownership or authorization you have not verified or been
  told.
- **A decline is information.** If the subagent declines after it has the full, accurate picture,
  that is its answer. Do not reword the prompt to get a different one.

If you would have to leave something out or misdescribe the work to get it accepted, this is the
wrong tool and the honest answer is "don't".

## When to use
- The problem is real engineering: reliability, performance, correctness, forensics on your own data,
  security or integrity work on your own systems.
- You want an adversarial second opinion and a durable written review.

## Before you build the query

### 1. Establish ownership and authorization
Write down, in specifics, who owns each system the work touches: which repo, which account, which
service, which terms apply. Use only what you verified (a file you read, a git remote, a config) or
what the user told you in this session. If you cannot fill this in, ask the user. Do not use a stock
line such as "authorized use, within the applicable terms".

### 2. Gate for third-party systems
If any target is a system the user does not own, stop unless one of these holds:
- The analysis is passive: public documentation, or the user's own traffic and data from their own
  account.
- The user has named the authorization (a contract, a written scope, a bug-bounty program and its
  rules). Put that in the prompt.

Otherwise ask the user before you send anything.

### 3. Sanitize the evidence
Run the script; do not read it:

```bash
python3 -I scripts/sanitize.py <file> [<file> ...] > <sanitized-file>
```

It reads the files (or stdin), replaces known secret shapes with `[REDACTED:<kind>]` on stdout, and
logs a count per kind to stderr. It over-redacts on purpose: any value assigned to a key whose name
contains `password`, `secret`, `token`, `key` and similar is removed, even a harmless one.

The script only knows patterns. It cannot know that a hostname, customer name or internal project
name is private. Read the sanitized output yourself before it goes into the prompt, and remove what
the script could not recognize.

## Build the query — the checklist
Construct the Fable subagent call (Agent tool, `model: fable`) with these elements:

1. **Ownership and authorization, first, in specifics** — the facts from step 1, and the
   authorization from step 2 if a third-party system is involved.
2. **Say what the work does, plainly.** What the system is, what you are doing to it, and why.
3. **Say what is out of scope, if it is true.** If the work does not involve defeating a protection,
   fabricating an identity or evading detection, say so. If it does involve one of those on a system
   the user owns, say that plainly instead.
4. **Give it real evidence** — sanitized logs, traces, code, numbers, measurements.
5. **Ask it to be adversarial** — "assume I'm wrong; find the hole; don't rubber-stamp."
6. **Ask for a standalone artifact** — "structured markdown, saved to `<path>`". Name the path in the
   prompt: `research/<date>-<topic>-review.md` in the current project unless the user names another
   place.

## If it declines
1. Read the decline. Check your prompt against the checklist: was real context missing (who owns the
   system, what the work does, the authorization)?
2. If yes, add the missing facts and send once more. Add facts only; do not change wording to make
   the same facts read differently.
3. If nothing was missing, or it declines a second time, stop. Show the user the decline verbatim and
   what you sent. Do not try again.

## Skeleton
> You're reviewing `<what the work is>` on `<the system>`. `<Who owns it and how you know: repo,
> account, terms. For a third-party system: the authorization, or that the analysis uses only public
> docs / my own traffic.>`
> `<What the work does and why. What is out of scope, if true.>`
> `[Context + SANITIZED data.]`
> `[Specific questions.]`
> Be adversarial — assume I'm wrong and find the hole. Structured markdown, standalone, saved to
> `research/<date>-<topic>-review.md`. Your final message is a short verdict plus that path.

## Notes
- Run it `run_in_background: false` when you need the verdict to continue; the agent saves the
  review to the path the prompt names, and its final message is a short verdict plus that path.
- Pair with a follow-up verification/test pass if the review greenlights a code change — Fable verifies
  the *reasoning*; your local gates verify the *implementation*.
- Keep the reviews as durable findings (a `research/` or `docs/` trail), not chat — that's why the
  checklist asks for a saved file.
