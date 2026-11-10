#!/usr/bin/env bash
# Copy skills from this repo into a skills directory. Safe by default:
# with no skill names it only reports what would change.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: ./install.sh <target> [--all | <skill> ...]

  ./install.sh <target>               report only: show what would change, change nothing
  ./install.sh <target> <skill> ...   install the named skills
  ./install.sh <target> --all         install every skill

A skill is a top-level folder of this repo that holds a SKILL.md.
Installing makes <target>/<skill> an exact copy (rsync --delete).
A <target>/<skill> that is a symlink is always skipped.
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

is_skill() {
  local s
  for s in ${SKILLS[@]+"${SKILLS[@]}"}; do
    [[ "$s" == "$1" ]] && return 0
  done
  return 1
}

MODE=report
SELECTED=()
if [[ $# -eq 1 && "$1" == "--all" ]]; then
  MODE=install
  SELECTED=(${SKILLS[@]+"${SKILLS[@]}"})
elif [[ $# -gt 0 ]]; then
  MODE=install
  bad=()
  for arg in "$@"; do
    if is_skill "$arg"; then SELECTED+=("$arg"); else bad+=("$arg"); fi
  done
  if [[ ${#bad[@]} -gt 0 ]]; then
    log "unknown skill(s): ${bad[*]}; installing nothing"
    echo "error: not a skill: ${bad[*]}" >&2
    echo "valid skills: ${SKILLS[*]:-none}" >&2
    exit 2
  fi
else
  SELECTED=(${SKILLS[@]+"${SKILLS[@]}"})
fi
log "mode=$MODE selected: ${SELECTED[*]:-none}"

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

if [[ "$MODE" == report ]]; then
  echo "Report only: nothing was changed. Install with: $0 $TARGET <skill> ...  (or --all)"
else
  log "done"
fi
