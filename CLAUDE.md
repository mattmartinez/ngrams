# ngrams: working rules

A library of Claude Code skills. Each top-level folder that holds a `SKILL.md` is one skill and installs on its own. Folders ending in `-workspace/` are private scratch space and are gitignored.

## Rules

- **Nothing private in tracked files or commit messages.** No company or product names, ticket IDs, hostnames, home-directory paths, or service, table and class names from a real codebase. Conventions that belong to one codebase go in a local config file that the skill reads (see `queryquant/references/scanner.md`), never in the skill itself.
- **Scripts for mechanical steps, prose for judgment.** A step that runs the same way every time ships as a script in the skill's `scripts/` folder with a `test_*.py` beside it, and `SKILL.md` says "run it, do not read it". Scripts use the Python standard library only and log each step to stderr.
- **Short answer first.** A skill's reply opens with a ranked list in plain words, one line per finding with `file:line`. Detail comes on request as a short card: what is wrong, the fix, the cost or risk, how to check it worked. Evidence goes last.
- **Each skill stands alone.** Skills install as separate folders, so one skill cannot load a file from another. `bug-hunt` and `red-team` share `scripts/resolve_target.py`; the two copies must stay identical, and `check.sh` enforces it.
- **One skill per commit.** Repo-level files (`install.sh`, `check.sh`, `README.md`, this file) get their own commits.
- **Shell code is BSD-safe and runs on bash 3.2**, the macOS default.

## Check your work

```bash
./check.sh
```

It runs every unit test, tests the installer in a temp folder, checks that the README lists every skill, and checks the shared script. Run it before each commit.

A wording change to a `bug-hunt` prompt also needs a benchmark run. Copy `bug-hunt-research/benchmarks/` to a folder outside the repo, so the agents cannot read the answer key, and run the skill on the copy. Score the Referee's reply with `bug-hunt-research/evaluate.py`. Keep the change only if the run still finds every planted bug and severity accuracy does not drop. Do not judge by the composite score: it moves by 0.09 between two runs of the same prompts, and `bug-hunt-research/insights.md` explains why. Budget for it: one run took about 2.5 hours at max effort.

To guard against private words, put one regular expression per line in a local `.private-words` file (gitignored). `check.sh` then fails if any of them appears in a tracked file or a commit message.

## Install

```bash
./install.sh ~/.claude/skills               # report only: changes nothing
./install.sh ~/.claude/skills <skill> ...   # install the skills you name
```

Edits here are inert until the skill is installed again. Use `--all` only on a machine where nothing else provides a skill of the same name.
