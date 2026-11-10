#!/usr/bin/env bash
# Copy skills and agent files from this repo into a harness config folder.
# Safe by default: with no names it only reports what would change.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: ./install.sh <target> [--all | <name> ...]

  ./install.sh <target>               report only: show what would change, change nothing
  ./install.sh <target> <name> ...    install the skills you name; "agents" installs the agent files
  ./install.sh <target> --all         install every skill and the agent files

A skill is a top-level folder of this repo that holds a SKILL.md.
Installing makes <target>/<skill> an exact copy (rsync --delete).
A <target>/<skill> that is a symlink is always skipped.

Agent files are the *.md files in this repo's agents/ folder. They go into
the "agents" folder beside <target>: for ~/.claude/skills that is
~/.claude/agents. A file already there with different content is first
copied to <file>.bak-<date>-<time> in the same folder, then replaced.
Other files in that folder are left alone. A target file that is a symlink
is always skipped.
EOF
}

log() { echo "[install] $*" >&2; }

case "${1:-}" in
  -h|--help) usage; exit 0 ;;
  "") usage >&2; exit 2 ;;
esac

TARGET="$1"
shift
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Same flags as before. `-i` itemizes changes; --delete-excluded also purges
# litter a previous install already shipped.
RSYNC_OPTS=(-ai --delete --delete-excluded
  --exclude=.bg-shell/ --exclude=__pycache__/ --exclude=.DS_Store
  --exclude=.gitignore --exclude=.python-version --exclude=evals/)

# Every top-level folder with a SKILL.md, in glob (sorted) order.
SKILLS=()
for dir in "$SCRIPT_DIR"/*/; do
  name="$(basename "$dir")"
  if [[ -f "$SCRIPT_DIR/$name/SKILL.md" ]]; then SKILLS+=("$name"); fi
done
log "repo=$SCRIPT_DIR target=$TARGET skills found: ${SKILLS[*]:-none}"

# Agent files: every *.md directly inside agents/. They are copied one by one
# into the "agents" folder beside <target>. That folder also holds agents this
# repo does not own, so nothing in it is ever deleted.
AGENTS_SRC="$SCRIPT_DIR/agents"
AGENTS_DST="$(dirname "$TARGET")/agents"
AGENT_FILES=()
if [[ -d "$AGENTS_SRC" ]]; then
  for path in "$AGENTS_SRC"/*.md; do
    if [[ -f "$path" ]]; then AGENT_FILES+=("$(basename "$path")"); fi
  done
fi
log "agents folder=$AGENTS_DST agent files found: ${AGENT_FILES[*]:-none}"

is_skill() {
  local s
  for s in ${SKILLS[@]+"${SKILLS[@]}"}; do
    [[ "$s" == "$1" ]] && return 0
  done
  return 1
}

MODE=report
SELECTED=()
DO_AGENTS=0
if [[ $# -eq 1 && "$1" == "--all" ]]; then
  MODE=install
  SELECTED=(${SKILLS[@]+"${SKILLS[@]}"})
  DO_AGENTS=1
elif [[ $# -gt 0 ]]; then
  MODE=install
  bad=()
  for arg in "$@"; do
    if [[ "$arg" == agents ]]; then
      DO_AGENTS=1
    elif is_skill "$arg"; then
      SELECTED+=("$arg")
    else
      bad+=("$arg")
    fi
  done
  if [[ ${#bad[@]} -gt 0 ]]; then
    log "unknown name(s): ${bad[*]}; installing nothing"
    echo "error: not a skill: ${bad[*]}" >&2
    echo "valid skills: ${SKILLS[*]:-none}" >&2
    echo "the name \"agents\" installs the agent files" >&2
    exit 2
  fi
else
  SELECTED=(${SKILLS[@]+"${SKILLS[@]}"})
  DO_AGENTS=1
fi
log "mode=$MODE selected: ${SELECTED[*]:-none} agents=$DO_AGENTS"

for name in ${SELECTED[@]+"${SELECTED[@]}"}; do
  src="$SCRIPT_DIR/$name/"
  dst="$TARGET/$name"

  if [[ -L "$dst" ]]; then
    log "skip $name: $dst is a symlink"
    echo "WARNING: skipped $name: $dst is a symlink (left untouched)" >&2
    echo "$name: skipped (target is a symlink)"
    continue
  fi

  if [[ "$MODE" == report ]]; then
    if [[ ! -d "$dst" ]]; then
      log "$name: $dst does not exist"
      echo "$name: not installed"
      continue
    fi
    log "rsync -n ${RSYNC_OPTS[*]} $src $dst/"
    changes="$(rsync -n "${RSYNC_OPTS[@]}" "$src" "$dst/")"
    if [[ -z "$changes" ]]; then
      echo "$name: in sync"
    else
      echo "$name: would change"
      echo "$changes" | sed 's/^/    /'
    fi
  else
    mkdir -p "$dst"
    log "rsync ${RSYNC_OPTS[*]} $src $dst/"
    echo "$name: installing"
    rsync "${RSYNC_OPTS[@]}" "$src" "$dst/" | sed 's/^/    /'
  fi
done

if [[ $DO_AGENTS -eq 1 ]]; then
  if [[ ${#AGENT_FILES[@]} -eq 0 ]]; then
    log "no agent files in $AGENTS_SRC"
    echo "agents: none in this repo"
  else
    echo "agents folder: $AGENTS_DST"
  fi
  STAMP="$(date +%Y%m%d-%H%M%S)"

  for file in ${AGENT_FILES[@]+"${AGENT_FILES[@]}"}; do
    src="$AGENTS_SRC/$file"
    dst="$AGENTS_DST/$file"
    label="agents/$file"

    if [[ -L "$dst" ]]; then
      log "skip $label: $dst is a symlink"
      echo "WARNING: skipped $label: $dst is a symlink (left untouched)" >&2
      echo "$label: skipped (target is a symlink)"
      continue
    fi
    if [[ -e "$dst" && ! -f "$dst" ]]; then
      log "skip $label: $dst is not a regular file"
      echo "WARNING: skipped $label: $dst is not a regular file (left untouched)" >&2
      echo "$label: skipped (target is not a file)"
      continue
    fi

    if [[ ! -e "$dst" ]]; then
      if [[ "$MODE" == report ]]; then
        log "$label: $dst does not exist"
        echo "$label: not installed"
      else
        log "mkdir -p $AGENTS_DST"
        mkdir -p "$AGENTS_DST"
        log "cp $src $dst"
        cp "$src" "$dst"
        echo "$label: installed"
      fi
      continue
    fi

    if cmp -s "$src" "$dst"; then
      log "$label: $dst is identical"
      echo "$label: in sync"
      continue
    fi

    if [[ "$MODE" == report ]]; then
      log "$label: $dst differs"
      echo "$label: would replace (the current file is backed up first)"
      continue
    fi

    # The backup stays beside the file it copies. Its name must not end in
    # .md, so it cannot be read as a second agent with the same name.
    bak="$dst.bak-$STAMP"
    if [[ -e "$bak" || -L "$bak" ]]; then
      log "backup name is taken: $bak; replacing nothing"
      echo "error: backup file already exists: $bak" >&2
      exit 1
    fi
    log "backup: cp -p $dst $bak"
    cp -p "$dst" "$bak"
    if ! cmp -s "$dst" "$bak"; then
      log "backup does not match $dst; replacing nothing"
      echo "error: backup is not a copy of $dst: $bak" >&2
      exit 1
    fi
    log "cp $src $dst"
    cp "$src" "$dst"
    echo "$label: replaced (previous file kept as $(basename "$bak"))"
  done
fi

if [[ "$MODE" == report ]]; then
  echo "Report only: nothing was changed. Install with: $0 $TARGET <skill> ...  (or agents, or --all)"
else
  log "done"
fi
