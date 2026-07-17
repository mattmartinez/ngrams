# ngrams

Cognitive patterns for AI coding agents. Portable skill modules that slot into any agent harness — specialized knowledge, encoded once, loaded on demand.

## Projects

| Project | Purpose |
|---|---|
| **[autoresearch](autoresearch/)** | Autonomous LLM training research agent. Fork of [karpathy/autoresearch](https://github.com/karpathy/autoresearch) with 21 cherry-picked community improvements (security, observability, robustness, agent strategy). Also ships a `SKILL.md` and installs as the `/autoresearch` skill via `install.sh`. |

## usage.py — Claude activity monitor

A simple read-only monitor you can leave running in a terminal tab to watch Claude Code activity. It tails every session and subagent transcript under `~/.claude/projects` and prints a live timeline: which model actually served each turn, token counts, estimated API-equivalent cost, and any model switches (including refusal fallbacks like Fable → Opus).

```bash
python3 usage.py                 # live tail of the last 24h (Ctrl-C to stop)
python3 usage.py --once          # print recent timeline + summary, then exit
python3 usage.py --all           # scan all history instead of the 24h default
python3 usage.py --daily         # per-day rollup: turns, tokens, cost, switches
python3 usage.py --fallbacks     # only show model switches + user turns
python3 usage.py --filter ngrams # only sessions matching a project/session substring
python3 usage.py --since 7d      # look back 7 days (also 12h, 30m, today, ISO dates)
```

No dependencies beyond the Python 3 standard library. In live mode a per-model usage summary stays pinned at the top of the terminal while the timeline scrolls below it. Costs are estimated at current API list rates — what the usage *would* bill on the API, not what a subscription charges.

## Skills

| Skill | Purpose |
|---|---|
| **bug-hunt** | Adversarial 3-agent bug sweep (Hunter → Skeptic → Referee) |
| **bug-hunt-research** | Autonomous prompt optimization loop for bug-hunt. Runs bug-hunt against benchmark codebases with planted bugs & traps, scores results, keeps or reverts prompt changes. Modeled on autoresearch. |
| **fablequery** | Frame a genuinely-engineering problem accurately so a Fable 5 subagent engages with rigor instead of a spurious safety decline. For legitimate gray-area work (own-systems automation, comparative analysis, integrity mechanics) — accurate framing, not circumvention. |
| **jenkins** | Interact with Jenkins CI/CD — list jobs, check build status, trigger builds, read console output, diagnose failures. Credentials stored in `~/.claude/jenkins.env`. |
| **jira** | Create and search Jira tickets from any terminal session. Credentials stored in `~/.claude/jira.env`, project aliases in `~/.claude/jira-projects.json`. Say "make this a Jira ticket" and it handles the rest. |
| **mdthis** | Generate markdown artifacts from conversation context |
| **red-team** | Adversarial 3-agent attack-path assessment (Attacker → Blue Team → Arbiter). Enumerates attack surface, builds realistic attack chains, and verifies which are actually exploitable. Generic across project shapes (service, CLI, library, daemon, data pipeline, IaC). |
| **slidedeck** | Build polished, self-contained HTML slide deck presentations from source material |

## Install

Sync each skill into your Claude Code skills directory:

```bash
./install.sh ~/.claude/skills
```

> **Note:** `install.sh` copies every skill directory into the target. `~/.claude/skills` is the default skill directory for [Claude Code](https://claude.com/claude-code); point it elsewhere if your harness uses a different location.
>
> Edits in this repo are inert until you re-run `install.sh` — run it after every skill change to keep `~/.claude/skills` in sync. The sync is one-way (repo → target) and itemizes what it changes.

## One-time setup for skills with external APIs

Some skills need credentials stored outside any repo:

| Skill | File | What to put in it |
|-------|------|-------------------|
| **jira** | `~/.claude/jira.env` | `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN` |
| | `~/.claude/jira-projects.json` | alias → project key map |
| **jenkins** | `~/.claude/jenkins.env` | `JENKINS_URL`, `JENKINS_USER`, `JENKINS_API_TOKEN` |

Run `/jira setup` or `/jenkins setup` in any session to be walked through credential setup interactively.

## Structure

Each skill is a directory with a `SKILL.md` entry point and optional supporting files (prompts, references). Skills are agent-agnostic — they describe *what to do*, not *which tool to call*.
