#!/bin/bash
# Status line for Claude Code: account email · model · working folder.
# The account is read from the config folder in use (CLAUDE_CONFIG_DIR, else
# the home folder). Needs jq.
#
# Optional: export STATUSLINE_WORK_DOMAIN=example.com to show accounts on that
# mail domain in red ("work"). Every other account shows in blue.
input=$(cat)
if [ -n "${CLAUDE_CONFIG_DIR:-}" ]; then
  cfg="$CLAUDE_CONFIG_DIR/.claude.json"
else
  cfg="$HOME/.claude.json"
fi
email=$(jq -r '.oauthAccount.emailAddress // "no-account"' "$cfg" 2>/dev/null)
if [ -z "$email" ]; then email="no-account"; fi
model=$(echo "$input" | jq -r '.model.display_name // ""' 2>/dev/null)
dir=$(echo "$input" | jq -r '.workspace.current_dir // ""' 2>/dev/null | sed "s|^$HOME|~|")

color='\033[38;2;66;165;245m'; ws_color=Blue
if [ -n "${STATUSLINE_WORK_DOMAIN:-}" ]; then
  case "$email" in
    *@"$STATUSLINE_WORK_DOMAIN") color='\033[38;2;192;57;43m'; ws_color=Red ;;
  esac
fi

# Inside a cmux workspace, tint the workspace to match. Only call cmux when the colour changes.
if [ -n "${CMUX_WORKSPACE_ID:-}" ] && command -v cmux >/dev/null 2>&1; then
  mark="${TMPDIR:-/tmp}/cmux-ws-color-$CMUX_WORKSPACE_ID"
  if [ "$(cat "$mark" 2>/dev/null)" != "$ws_color" ]; then
    (cmux workspace-action --action set-color --color "$ws_color" >/dev/null 2>&1 \
      && echo "$ws_color" > "$mark") &
  fi
fi
printf "${color}%s\033[0m · %s · \033[2m%s\033[0m" "$email" "$model" "$dir"
