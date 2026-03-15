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
| **mdthis** | Generate markdown artifacts from conversation context |

## Install

Symlink into your agent's skill directory:

```bash
./install.sh ~/.gsd/agent/skills     # pi + GSD 2.0
./install.sh ~/.claude/skills         # claude code
```

> **Note:** The `~/.gsd/agent/skills` path is specific to the [GSD](https://github.com/gsd-framework/gsd) extension for [pi](https://github.com/anthropics/pi). Other pi extensions may use a different skill directory.

## Structure

Each skill is a directory with a `SKILL.md` entry point and optional supporting files (prompts, references). Skills are agent-agnostic — they describe *what to do*, not *which tool to call*.
