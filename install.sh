#!/usr/bin/env bash
set -euo pipefail

TARGET="${1:?Usage: ./install.sh <skill-directory>}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

mkdir -p "$TARGET"

for skill in "$SCRIPT_DIR"/*/; do
  name="$(basename "$skill")"
  [[ "$name" == ".git" ]] && continue

  if [[ -L "$TARGET/$name" ]]; then
    rm "$TARGET/$name"
  fi

  mkdir -p "$TARGET/$name"
  rsync -a --delete "$skill" "$TARGET/$name/"
  echo "  sync  $name → $TARGET/$name"
done
