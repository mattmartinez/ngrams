#!/usr/bin/env bash
set -euo pipefail

TARGET="${1:?Usage: ./install.sh <skill-directory>}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

mkdir -p "$TARGET"

for skill in "$SCRIPT_DIR"/*/; do
  name="$(basename "$skill")"
  [[ "$name" == ".git" ]] && continue

  if [[ -L "$TARGET/$name" ]]; then
    echo "  skip  $name (already linked)"
  elif [[ -d "$TARGET/$name" ]]; then
    echo "  skip  $name (directory exists — remove manually to re-link)"
  else
    ln -s "$skill" "$TARGET/$name"
    echo "  link  $name → $skill"
  fi
done
