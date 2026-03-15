# ngrams

Cognitive patterns for AI coding agents. Portable skill modules that slot into any agent harness — specialized knowledge, encoded once, loaded on demand.

## Skills

| Skill | Purpose |
|---|---|
| **bug-hunt** | Adversarial 3-agent bug sweep (Hunter → Skeptic → Referee) |
| **mdthis** | Generate markdown artifacts from conversation context |

## Install

Symlink into your agent's skill directory:

```bash
./install.sh ~/.gsd/agent/skills     # pi
./install.sh ~/.claude/skills         # claude code
```

## Structure

Each skill is a directory with a `SKILL.md` entry point and optional supporting files (prompts, references). Skills are agent-agnostic — they describe *what to do*, not *which tool to call*.
