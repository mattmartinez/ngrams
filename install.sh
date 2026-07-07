#!/usr/bin/env bash
set -euo pipefail

TARGET="${1:?Usage: ./install.sh <skill-directory>}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

mkdir -p "$TARGET"

# One-way sync: each skill dir → $TARGET/<skill>. `-i` itemizes what changed, so a
# silent skill is already up to date; anything printed is drift being corrected.
# Excludes keep working-tree litter out of the installed copy, and
# --delete-excluded also purges litter a previous install already shipped.
for skill in "$SCRIPT_DIR"/*/; do
  name="$(basename "$skill")"

  if [[ -L "$TARGET/$name" ]]; then
    rm "$TARGET/$name"
  fi

  mkdir -p "$TARGET/$name"
  rsync -ai --delete --delete-excluded \
    --exclude=.bg-shell/ --exclude=__pycache__/ --exclude=.DS_Store \
    --exclude=.gitignore --exclude=.python-version \
    "$skill" "$TARGET/$name/"
done

echo "Synced $(ls -d "$SCRIPT_DIR"/*/ | wc -l | tr -d ' ') skills → $TARGET (changes itemized above; silent = already up to date)"
