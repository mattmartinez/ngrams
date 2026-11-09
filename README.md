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
| **autoresearch** | Autonomous ML research loop for the bundled [autoresearch](autoresearch/) project: edits the training script, runs timed experiments, keeps what improves. Needs an NVIDIA GPU host. Invoke with `/autoresearch`. |
| **bug-hunt** | Adversarial 3-agent bug sweep (Hunter → Skeptic → Referee) |
| **bug-hunt-research** | Autonomous prompt optimization loop for bug-hunt. Runs bug-hunt against benchmark codebases with planted bugs & traps, scores results, keeps or reverts prompt changes. Modeled on autoresearch. |
| **claudish** | Manually rewrite the previous Claude response in plain English through local Ollama with `/claudish`; no automatic per-response invocation. |
| **fablequery** | Frame a genuinely-engineering problem accurately so a Fable subagent engages with rigor instead of a spurious safety decline. For legitimate gray-area work (own-systems automation, comparative analysis, integrity mechanics) — accurate framing, not circumvention. |
| **jira** | Create and search Jira tickets from any terminal session. Credentials stored in `~/.claude/jira.env`, project aliases in `~/.claude/jira-projects.json`. Say "make this a Jira ticket" and it handles the rest. |
| **mdthis** | Generate markdown artifacts from conversation context |
| **queryquant** | Review the SQL queries in a codebase (JDBI SQL Objects + Liquibase on Aurora MySQL first): lists N+1 loops, missing indexes, unbounded reads, catch-all filters, offset paging and unchunked batches worst first with file and line, then deep-dives one on request with an EXPLAIN you run and a short fix card. Never runs SQL itself. |
| **red-team** | Adversarial 3-agent attack-path assessment (Attacker → Blue Team → Arbiter). Enumerates attack surface, builds realistic attack chains, and verifies which are actually exploitable. Generic across project shapes (service, CLI, library, daemon, data pipeline, IaC). |
| **slidedeck** | Build polished, self-contained HTML slide deck presentations from source material |

## Install

`install.sh` copies skills from this repo into a skills directory. `~/.claude/skills` is the default for [Claude Code](https://claude.com/claude-code); point it elsewhere if your harness uses a different location.

```bash
./install.sh ~/.claude/skills                   # report only: shows what would change, changes nothing
./install.sh ~/.claude/skills bug-hunt mdthis   # install the skills you name
./install.sh ~/.claude/skills --all             # install every skill in this repo
```

> **Note:** A skill is any top-level folder here that holds a `SKILL.md`. Installing a skill makes the target folder an exact copy: files there that are not in this repo are deleted. Run the report first if the target might already hold a skill of the same name from somewhere else. A target that is a symlink is skipped, never replaced.
>
> Edits in this repo are inert until you install the skill again. The copy is one-way (repo → target).

### Claudish: manual local plain-English rewrites

`/claudish` rewrites the immediately preceding Claude response through Ollama
only when you request it. Unlike the original `claudish-to-english` display
hook, it does not run after every response.

Requirements:

- Python 3 on the Claude Code machine; no third-party Python packages.
- Ollama reachable from that machine with the configured model installed.
- The automatic `claudish-to-english` plugin disabled if it is still installed.

To install only this skill from PowerShell:

```powershell
$target = Join-Path $HOME '.claude\skills\claudish'
New-Item -ItemType Directory -Force $target | Out-Null
Copy-Item '.\claudish\*' $target -Recurse -Force
```

The defaults target `gemma4:12b` at `http://localhost:11434`. If Ollama runs on
another machine, override the endpoint in the `env` object in
`~/.claude/settings.json`. The address below uses the reserved documentation IP
`192.0.2.10`; replace it locally with the Ollama machine's private LAN address:

```json
{
  "env": {
    "CLAUDISH_OLLAMA": "http://192.0.2.10:11434",
    "CLAUDISH_MODEL": "gemma4:12b",
    "CLAUDISH_TIMEOUT": "120"
  }
}
```

Restart Claude Code after changing its environment settings. After Claude
answers, run:

```text
/claudish
```

The script reads the prior assistant response from Claude Code's local JSONL
transcript and sends it directly to Ollama. Ollama performs the rewrite. The
rewritten result is then inserted into the `/claudish` turn so Claude can return
it verbatim; consequently, that result becomes part of the cloud conversation.
Invoking the skill uses a small Claude turn, but the rewriting prompt and
inference run locally.

## One-time setup for skills with external APIs

Some skills need credentials stored outside any repo:

| Skill | File | What to put in it |
|-------|------|-------------------|
| **jira** | `~/.claude/jira.env` | `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN` |
| | `~/.claude/jira-projects.json` | alias → project key map |
| **claudish** | Python 3 + `~/.claude/settings.json` (`env`) | Uses only the Python standard library. Optional `CLAUDISH_OLLAMA`, `CLAUDISH_MODEL`, and `CLAUDISH_TIMEOUT`; defaults are `http://localhost:11434`, `gemma4:12b`, and `120`. |

Run `/jira setup` in any session to be walked through credential setup interactively.

## Structure

Each skill is a directory with a `SKILL.md` entry point and optional supporting files (prompts, references). Skills are agent-agnostic — they describe *what to do*, not *which tool to call*.
