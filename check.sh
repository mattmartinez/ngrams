#!/usr/bin/env bash
# Run every check for this repo of Claude Code skills.
# Prints one "ok" / "FAIL" / "skip" line per check on stdout, with detail
# under each FAIL, then a summary. Exits 0 only if every check passed.
# Step-by-step trace goes to stderr with a [check] prefix.
# Usage: ./check.sh   (works from any directory; takes no options)
set -uo pipefail

log() { echo "[check] $*" >&2; }

REPO="$(cd "$(dirname "$0")" && pwd -P)" || { echo "cannot find repo folder" >&2; exit 1; }
cd "$REPO" || { echo "cannot cd to $REPO" >&2; exit 1; }
log "repo=$REPO"

TMP="$(mktemp -d)" || { echo "mktemp -d failed" >&2; exit 1; }
cleanup() {
  # Only ever remove the folder mktemp -d returned.
  if [[ -n "$TMP" && -d "$TMP" ]]; then
    log "removing temp folder $TMP"
    rm -rf "$TMP"
  fi
}
trap cleanup EXIT
log "temp folder $TMP"

if [[ -x /bin/bash ]]; then BASH_CMD=/bin/bash; else BASH_CMD=bash; fi
log "installer shell: $BASH_CMD"

N_OK=0
N_SKIP=0
N_FAIL=0

pass() { echo "ok    $1"; N_OK=$((N_OK + 1)); log "result ok: $1"; }
skip() { echo "skip  $1"; N_SKIP=$((N_SKIP + 1)); log "result skip: $1"; }
# fail <name> [detail file]: print FAIL and the detail file, indented.
fail() {
  echo "FAIL  $1"
  N_FAIL=$((N_FAIL + 1))
  log "result FAIL: $1"
  if [[ $# -ge 2 && -s "$2" ]]; then sed 's/^/      /' "$2"; fi
}

# Every top-level folder with a SKILL.md, sorted, one per line.
list_skills() {
  local d
  for d in */; do
    d="${d%/}"
    if [[ -f "$d/SKILL.md" ]]; then echo "$d"; fi
  done | LC_ALL=C sort
}

# 1. Unit tests: every test_*.py outside .git/ and top-level *-workspace folders.
check_unit_tests() {
  log "check 1: unit tests"
  local tests=() p top out rc
  while IFS= read -r p; do
    top="${p#./}"
    top="${top%%/*}"
    case "$top" in
      .git|*-workspace) log "  ignore $p (under $top)"; continue ;;
    esac
    tests+=("${p#./}")
  done < <(find . -name 'test_*.py' -type f | LC_ALL=C sort)

  if [[ ${#tests[@]} -eq 0 ]]; then
    log "  no test files found"
    echo "no test_*.py files found" > "$TMP/unit.out"
    fail "unit tests (none found)" "$TMP/unit.out"
    return
  fi

  for p in "${tests[@]}"; do
    out="$TMP/unit.out"
    log "  run: python3 -I $p"
    python3 -I "$p" > "$out" 2>&1
    rc=$?
    log "  $p exit=$rc"
    if [[ $rc -eq 0 ]]; then
      pass "unit test $p"
    else
      tail -n 20 "$out" > "$out.tail"
      fail "unit test $p (exit $rc)" "$out.tail"
    fi
  done
}

# 2. Installer: syntax, --all, report mode changes nothing, symlink kept, unknown name.
check_installer() {
  log "check 2: installer"
  local name="installer" det="$TMP/inst.detail" out="$TMP/inst.out" rc
  local t="$TMP/t" want="$TMP/want.txt" got="$TMP/got.txt"
  local skills=() s first second
  : > "$det"

  if [[ -x /bin/bash ]]; then
    log "  /bin/bash -n install.sh"
    if ! /bin/bash -n install.sh > "$out" 2>&1; then
      { echo "/bin/bash -n install.sh failed:"; tail -n 10 "$out"; } >> "$det"
    fi
  fi

  list_skills > "$want"
  while IFS= read -r s; do skills+=("$s"); done < "$want"
  log "  skills in repo: ${skills[*]:-none}"
  if [[ ${#skills[@]} -lt 2 ]]; then
    echo "need at least 2 skills to test the installer, found ${#skills[@]}" >> "$det"
    fail "$name" "$det"
    return
  fi
  first="${skills[0]}"
  second="${skills[1]}"

  # a. --all installs exactly the repo's skills.
  mkdir -p "$t"
  log "  a. $BASH_CMD install.sh $t --all"
  "$BASH_CMD" install.sh "$t" --all > "$out" 2>&1
  rc=$?
  log "  a. exit=$rc"
  if [[ $rc -ne 0 ]]; then
    { echo "a. install --all exited $rc:"; tail -n 10 "$out"; } >> "$det"
  fi
  ( cd "$t" && for s in */; do [[ -d "$s" ]] && echo "${s%/}"; done ) | LC_ALL=C sort > "$got"
  if ! diff "$want" "$got" > "$out" 2>&1; then
    { echo "a. installed folders differ from repo skills (< repo, > installed):"; cat "$out"; } >> "$det"
  fi

  # b. Report mode must not touch an edited skill, a foreign skill, or a symlink.
  log "  b. plant: edit $first/SKILL.md, add foreign/, symlink $second"
  echo "local edit planted by check.sh" >> "$t/$first/SKILL.md"
  mkdir -p "$t/foreign" "$TMP/empty"
  echo "foreign skill planted by check.sh" > "$t/foreign/SKILL.md"
  mv "$t/$second" "$TMP/moved-$second"
  ln -s "$TMP/empty" "$t/$second"
  cp -R "$t" "$TMP/t-copy"
  log "  b. $BASH_CMD install.sh $t (report mode)"
  "$BASH_CMD" install.sh "$t" > "$out" 2>&1
  rc=$?
  log "  b. exit=$rc"
  if [[ $rc -ne 0 ]]; then
    { echo "b. report mode exited $rc:"; tail -n 10 "$out"; } >> "$det"
  fi
  if ! diff -r "$t" "$TMP/t-copy" > "$out" 2>&1; then
    { echo "b. report mode changed the target:"; tail -n 20 "$out"; } >> "$det"
  fi

  # c. Installing the symlinked skill by name leaves the symlink alone.
  log "  c. $BASH_CMD install.sh $t $second"
  "$BASH_CMD" install.sh "$t" "$second" > "$out" 2>&1
  rc=$?
  log "  c. exit=$rc"
  if [[ ! -L "$t/$second" ]]; then
    echo "c. $second is no longer a symlink after install by name" >> "$det"
  fi
  if [[ -n "$(find "$TMP/empty" -mindepth 1 -print 2>/dev/null | head -n 1)" ]]; then
    echo "c. the symlinked folder is no longer empty after install by name" >> "$det"
  fi

  # d. Unknown skill name exits 2.
  log "  d. $BASH_CMD install.sh $t no-such-skill-zz"
  "$BASH_CMD" install.sh "$t" no-such-skill-zz > "$out" 2>&1
  rc=$?
  log "  d. exit=$rc"
  if [[ $rc -ne 2 ]]; then
    { echo "d. unknown skill name exited $rc, want 2:"; tail -n 10 "$out"; } >> "$det"
  fi

  if [[ -s "$det" ]]; then fail "$name" "$det"; else pass "$name"; fi
}

# 3. README table: every skill has a "| **<name>** |" row.
check_readme() {
  log "check 3: README table"
  local det="$TMP/readme.detail" s
  : > "$det"
  while IFS= read -r s; do
    if ! grep -q -F "| **$s** |" README.md; then
      log "  no README row for $s"
      echo "missing README row: | **$s** |" >> "$det"
    fi
  done < <(list_skills)
  if [[ -s "$det" ]]; then fail "README table" "$det"; else pass "README table"; fi
}

# 4. Shared scripts: the two resolve_target.py copies must be byte-identical.
check_shared_scripts() {
  log "check 4: shared scripts"
  local a="bug-hunt/scripts/resolve_target.py" b="red-team/scripts/resolve_target.py"
  local det="$TMP/shared.detail" f
  : > "$det"
  for f in "$a" "$b"; do
    if [[ ! -f "$f" ]]; then echo "missing: $f" >> "$det"; fi
  done
  if [[ ! -s "$det" ]] && ! cmp -s "$a" "$b"; then
    echo "differ: $a and $b" >> "$det"
  fi
  if [[ -s "$det" ]]; then fail "shared scripts" "$det"; else pass "shared scripts"; fi
}

# 5. Private words: patterns in .private-words must not appear in tracked files or commit messages.
check_private_words() {
  log "check 5: private words"
  local name="private words" pat="$TMP/private-words.pat" det="$TMP/pw.detail" out="$TMP/pw.out" rc
  if [[ ! -f .private-words ]]; then
    skip "$name (.private-words not present)"
    return
  fi
  # Drop blank and comment lines: an empty pattern would match everything.
  grep -v -E '^#|^[[:space:]]*$' .private-words > "$pat"
  log "  $(wc -l < "$pat" | tr -d ' ') pattern(s) after cleaning"
  if [[ ! -s "$pat" ]]; then
    log "  no patterns left after cleaning"
    pass "$name (no patterns)"
    return
  fi
  : > "$det"

  log "  git grep tracked files"
  git grep -n -i -E -f "$pat" -- . > "$out" 2>&1
  rc=$?
  log "  git grep exit=$rc"
  if [[ $rc -eq 0 ]]; then
    { echo "tracked files:"; sed 's/^/  /' "$out"; } >> "$det"
  elif [[ $rc -ne 1 ]]; then
    { echo "git grep failed (exit $rc):"; tail -n 10 "$out"; } >> "$det"
  fi

  log "  git log --all commit messages"
  if git log --all --format=%B > "$TMP/messages.txt" 2> "$out"; then
    grep -n -i -E -f "$pat" "$TMP/messages.txt" > "$out" 2>&1
    rc=$?
    log "  message grep exit=$rc"
    if [[ $rc -eq 0 ]]; then
      { echo "commit messages (line in git log --all --format=%B):"; sed 's/^/  /' "$out"; } >> "$det"
    elif [[ $rc -ne 1 ]]; then
      { echo "grep on commit messages failed (exit $rc):"; tail -n 10 "$out"; } >> "$det"
    fi
  else
    { echo "git log failed:"; tail -n 10 "$out"; } >> "$det"
  fi

  if [[ -s "$det" ]]; then fail "$name" "$det"; else pass "$name"; fi
}

# 6. queryquant scan scorer: full recall and no trap violations (local workspace only).
check_queryquant_scan() {
  log "check 6: queryquant scan scorer"
  local name="queryquant scan scorer" scorer="queryquant-workspace/evalkit/score_scan.py"
  local json="$TMP/scan.json" det="$TMP/scan.detail" rc
  if [[ ! -f "$scorer" ]]; then
    skip "$name ($scorer not present)"
    return
  fi
  log "  run: python3 -I $scorer --json"
  python3 -I "$scorer" --json > "$json" 2> "$TMP/scan.err"
  rc=$?
  log "  scorer exit=$rc"
  python3 -I -c '
import json, sys
try:
    d = json.load(open(sys.argv[1]))
    r = d["recall"]["ratio"]
    c = d["trap_violations"]["count"]
except Exception as e:
    print("cannot read scorer JSON: %r" % (e,))
    sys.exit(1)
print("recall.ratio=%s trap_violations.count=%s" % (r, c))
sys.exit(0 if r == 1.0 and c == 0 else 1)
' "$json" > "$det" 2>&1
  if [[ $? -eq 0 ]]; then
    log "  $(cat "$det")"
    pass "$name"
  else
    { echo "scorer exit=$rc"; tail -n 10 "$TMP/scan.err"; } >> "$det"
    fail "$name" "$det"
  fi
}

check_unit_tests
check_installer
check_readme
check_shared_scripts
check_private_words
check_queryquant_scan

TOTAL=$((N_OK + N_SKIP + N_FAIL))
if [[ $N_FAIL -eq 0 ]]; then
  echo "summary: all passed, $TOTAL checks: $N_OK ok, $N_SKIP skipped, 0 failed"
  log "done: all passed"
  exit 0
fi
echo "summary: FAILED, $TOTAL checks: $N_OK ok, $N_SKIP skipped, $N_FAIL failed"
log "done: $N_FAIL failed"
exit 1
