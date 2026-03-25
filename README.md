# ngrams

Cognitive patterns for AI coding agents. Portable skill modules that slot into any agent harness — specialized knowledge, encoded once, loaded on demand.

## Projects

| Project | Purpose |
|---|---|
| **[autoresearch](autoresearch/)** | Autonomous LLM training research agent. Fork of [karpathy/autoresearch](https://github.com/karpathy/autoresearch) with 21 cherry-picked community improvements (security, observability, robustness, agent strategy). |

## Skills

| Skill | Purpose |
|---|---|
| **bug-hunt** | Adversarial 3-agent bug sweep (Hunter → Skeptic → Referee) |
| **bug-hunt-research** | Autonomous prompt optimization loop for bug-hunt. Runs bug-hunt against benchmark codebases with planted bugs & traps, scores results, keeps or reverts prompt changes. Modeled on autoresearch. |
| **cmux** | Claude Code integration with cmux terminal multiplexer — splits, workspaces, sidebar, notifications |
| **jira** | Create and search Jira tickets from any terminal session. Credentials stored in `~/.gsd/jira.env`, project aliases in `~/.gsd/jira-projects.json`. Say "make this a Jira ticket" and it handles the rest. |
| **mdthis** | Generate markdown artifacts from conversation context |
| **slidedeck** | Build polished, self-contained HTML slide deck presentations from source material |

## Install

Symlink into your agent's skill directory:

```bash
./install.sh ~/.gsd/agent/skills     # pi + GSD 2.0
./install.sh ~/.claude/skills         # claude code
```

> **Note:** The `~/.gsd/agent/skills` path is specific to the [GSD](https://github.com/gsd-framework/gsd) extension for [pi](https://github.com/anthropics/pi). Other pi extensions may use a different skill directory.

## One-time setup for skills with external APIs

Some skills need credentials stored outside any repo:

| Skill | File | What to put in it |
|-------|------|-------------------|
| **jira** | `~/.gsd/jira.env` | `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN` |
| | `~/.gsd/jira-projects.json` | alias → project key map |

Run `/jira setup` in any session to be walked through the Jira credential setup interactively.

## Structure

Each skill is a directory with a `SKILL.md` entry point and optional supporting files (prompts, references). Skills are agent-agnostic — they describe *what to do*, not *which tool to call*.
