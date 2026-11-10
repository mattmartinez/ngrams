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
| **fablequery** | Hand an engineering problem to a Fable subagent for a well-scoped adversarial review: plain statement of what the system is, who owns it and what the work does, sanitized evidence, and a saved review. For systems you own or are authorized to test. |
| **mdthis** | Generate markdown artifacts from conversation context |
| **queryquant** | Review the SQL queries in a codebase (JDBI SQL Objects + Liquibase on Aurora MySQL first): lists N+1 loops, missing indexes, unbounded reads, catch-all filters, offset paging and unchunked batches worst first with file and line, then deep-dives one on request with an EXPLAIN you run and a short fix card. Never runs SQL itself. |
| **red-team** | Adversarial three-stage attack-path assessment (Attacker → Blue Team → Arbiter) with independent coverage synthesis. Maps realistic attack chains, checks exploitability from source, and reports residual uncertainty and coverage. Static and read-only across services, CLIs, libraries, daemons, data pipelines, and IaC. |
| **slidedeck** | Build polished, self-contained HTML slide deck presentations from source material |

## Agents

| Agent | Purpose |
|---|---|
| **researcher** | Pulls current docs for a library, API, CLI or cloud service and quotes the exact guidance with sources. Never edits. |
| **worker** | Makes independent, scoped changes that can run in parallel, runs the repo's verify command and reports the real output. |

Each agent is one Markdown file in [agents/](agents/), written as a Claude Code subagent definition.

## Settings

[settings/](settings/) holds an example Claude Code `settings.json` and the status line script it points at. `install.sh` does not install them: a settings file has to be merged into your own by hand, not overwritten.

| File | What it is |
|---|---|
| **settings.json** | Example user settings. `advisorModel` gives the main model an `advisor` tool: when the model calls it, the whole conversation goes to that stronger model for a second opinion. The file also sets the main model, effort levels, two `PreToolUse` hooks, two plugins and the status line. |
| **statusline.sh** | Prints account email · model · working folder. Copy it to `~/.claude/statusline.sh`, which is where the example settings look for it. |

Before you copy anything from it:

- The example runs in `auto` permission mode and skips two confirmation prompts. Leave those keys out if you want the prompts.
- The `Bash` hook needs `rtk` on your `PATH`, the `Edit|Write` hook needs `node`, and the status line needs `jq`. Leave out what you don't use.
- The `researcher` agent uses the tools of the `context7` plugin that the example enables.
- Values that belong to one machine are left out on purpose. Put your own in an `env` block in your local settings.
- To show one mail domain as "work" in the status line, export `STATUSLINE_WORK_DOMAIN` (for example `example.com`) in your shell profile.

## Install

`install.sh` copies skills from this repo into a skills directory. `~/.claude/skills` is the default for [Claude Code](https://claude.com/claude-code); point it elsewhere if your harness uses a different location.

```bash
./install.sh ~/.claude/skills                   # report only: shows what would change, changes nothing
./install.sh ~/.claude/skills bug-hunt mdthis   # install the skills you name
./install.sh ~/.claude/skills agents            # install the agent files into ~/.claude/agents
./install.sh ~/.claude/skills --all             # install every skill and the agent files
```

> **Note:** A skill is any top-level folder here that holds a `SKILL.md`. Installing a skill makes the target folder an exact copy: files there that are not in this repo are deleted. Run the report first if the target might already hold a skill of the same name from somewhere else. A target that is a symlink is skipped, never replaced.
>
> Edits in this repo are inert until you install the skill again. The copy is one-way (repo → target).
>
> Agent files go into the `agents` folder beside the skills directory. If a file of the same name is already there with different content, the installer first copies it to `<file>.bak-<date>-<time>` in the same folder, then replaces it. Other files in that folder are never touched, and a target file that is a symlink is skipped.

### Red team: static attack-path assessment

Run `/red-team` only on code you own or are explicitly authorized to assess. It uses Attacker, Blue Team, and Arbiter stages, with independent coverage auditing and cross-zone chain synthesis. It reads source and configuration but does not send traffic, execute payloads, or modify the assessed project.

```text
/red-team                         # current directory
/red-team src                     # a directory or single file
/red-team --diff                  # changes from the default branch merge-base
/red-team --commit abc1234        # changes from a commit's first parent
/red-team --pr 42                 # changes in a GitHub pull request
```

Diff, commit, and PR assessments read an immutable snapshot of the requested commit, so local edits and later commits cannot contaminate the evidence. Python 3 and Git are required; PR mode also requires the GitHub CLI. Deleted files and supported manifest-only changes remain reviewable through the saved diff.

An optional `.red-team-profile.md` in the target or a parent directory supplies deployment context:

```markdown
# Crown jewels
- Tenant data and the ability to administer accounts

# In-scope attacker personas
- Unauthenticated internet user
- Authenticated low-privilege tenant
- Out of scope: physical access and malicious root

# Deployed controls
- Describe controls that exist outside the repository

# Severity overrides
- Describe domain-specific escalation rules
```

The short answer lists exploitable paths, mitigations, unresolved evidence, and coverage exclusions. Ask `look at N`, `what stopped the rest`, or `full report` for details. “Exploitable” means supported by a static code path; it is not a claim that a live exploit was executed. Secret values are redacted from reports.

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

Some skills need settings stored outside any repo:

| Skill | File | What to put in it |
|-------|------|-------------------|
| **claudish** | Python 3 + `~/.claude/settings.json` (`env`) | Uses only the Python standard library. Optional `CLAUDISH_OLLAMA`, `CLAUDISH_MODEL`, and `CLAUDISH_TIMEOUT`; defaults are `http://localhost:11434`, `gemma4:12b`, and `120`. |

## Structure

Each skill is a directory with a `SKILL.md` entry point and optional supporting files (prompts, references, scripts). The skills are written for Claude Code and name its tools where a step needs one. The instructions are plain Markdown, so most port to another harness with small edits.
