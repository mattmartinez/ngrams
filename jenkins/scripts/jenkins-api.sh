#!/usr/bin/env bash
# jenkins-api.sh — Thin Jenkins REST client for agent use.
#
# Reads credentials from env vars (source ~/.claude/jenkins.env before calling).
#
# Commands:
#   whoami                             Verify auth, print Jenkins version
#   folders                            List top-level folders/jobs
#   jobs <folder>                      List jobs in a folder
#   branches <folder> <project>        List branches in a multibranch project
#   status <folder> <project> [branch] Last build status (default: development)
#   log <folder> <project> [branch] [build] Console output (default: lastBuild)
#   log-tail <folder> <project> [branch] [lines] Last N lines (default: 80)
#   build <folder> <project> [branch]  Trigger a build (default: development)
#   build-with-params <folder> <project> [branch] KEY=value [KEY=value ...]
#                                      Trigger a parameterized build via /buildWithParameters
#   health <folder>                    Health summary for all multibranch jobs in a folder
#
# Usage:
#   source ~/.claude/jenkins.env
#   ./jenkins-api.sh whoami
#   ./jenkins-api.sh jobs backend
#   ./jenkins-api.sh status backend my-service development
#   ./jenkins-api.sh log-tail backend my-service development 50
#   ./jenkins-api.sh build backend my-service development

set -uo pipefail

# ── Credentials ────────────────────────────────────────────────────────────────

JENKINS_URL="${JENKINS_URL:-}"
JENKINS_URL="${JENKINS_URL%/}"
JENKINS_USER="${JENKINS_USER:-}"
JENKINS_API_TOKEN="${JENKINS_API_TOKEN:-}"

require_creds() {
  local missing=()
  [[ -z "$JENKINS_URL" ]]       && missing+=("JENKINS_URL")
  [[ -z "$JENKINS_USER" ]]      && missing+=("JENKINS_USER")
  [[ -z "$JENKINS_API_TOKEN" ]] && missing+=("JENKINS_API_TOKEN")
  if [[ ${#missing[@]} -gt 0 ]]; then
    echo "❌ Missing credentials: ${missing[*]}" >&2
    echo "Run: source ~/.claude/jenkins.env" >&2
    exit 1
  fi
}

# ── HTTP helpers ───────────────────────────────────────────────────────────────
# Retry policy: HTTP 408/429/502/503 and curl exit 28 (timeout) trigger up to
# 3 retries with exponential backoff (1s, 2s, 4s). Each attempt is capped at
# 15s via --max-time. Auth (401/403) and other 4xx are NOT retried.

_jretryable() {
  local http="$1" curl_exit="$2"
  case "$http" in 408|429|502|503) return 0 ;; esac
  [[ "$curl_exit" == "28" ]] && return 0
  return 1
}

_jfail_msg() {
  local http="$1" curl_exit="$2"
  if [[ "$curl_exit" != "0" ]]; then
    echo "Connection failed — Jenkins may be slow or offline (curl exit $curl_exit)" >&2
    return
  fi
  case "$http" in
    401|403) echo "HTTP $http auth failed — token invalid or rotated; regenerate token / re-run setup" >&2 ;;
    404)     echo "HTTP 404 not found — check folder/project/branch names" >&2 ;;
    5*)      echo "HTTP $http Jenkins server error" >&2 ;;
  esac
}

jget() {
  local url="$1" max_time="${2:-15}"
  local retry=0 max_retries=3 sleep_s=1
  local body_file http curl_exit cause
  body_file=$(mktemp)
  while :; do
    http=$(curl -s --max-time "$max_time" -o "$body_file" -w '%{http_code}' \
      -u "$JENKINS_USER:$JENKINS_API_TOKEN" "$url")
    curl_exit=$?
    if [[ $curl_exit -eq 0 && "$http" =~ ^2 ]]; then
      cat "$body_file"
      rm -f "$body_file"
      return 0
    fi
    if (( retry < max_retries )) && _jretryable "$http" "$curl_exit"; then
      retry=$((retry + 1))
      cause="http $http"
      [[ "$curl_exit" == "28" ]] && cause="curl timeout"
      echo "WARNING: jenkins jget retry $retry/$max_retries after $cause" >&2
      sleep "$sleep_s"
      sleep_s=$((sleep_s * 2))
      continue
    fi
    _jfail_msg "$http" "$curl_exit"
    rm -f "$body_file"
    return 1
  done
}

jpost() {
  local url="$1"
  local retry=0 max_retries=3 sleep_s=1
  local http curl_exit cause
  while :; do
    http=$(curl -s --max-time 15 -o /dev/null -w '%{http_code}' -X POST \
      -u "$JENKINS_USER:$JENKINS_API_TOKEN" "$url")
    curl_exit=$?
    if [[ $curl_exit -eq 0 ]] && ! _jretryable "$http" "$curl_exit"; then
      printf '%s' "$http"
      return 0
    fi
    if (( retry < max_retries )) && _jretryable "$http" "$curl_exit"; then
      retry=$((retry + 1))
      cause="http $http"
      [[ "$curl_exit" == "28" ]] && cause="curl timeout"
      echo "WARNING: jenkins jpost retry $retry/$max_retries after $cause" >&2
      sleep "$sleep_s"
      sleep_s=$((sleep_s * 2))
      continue
    fi
    _jfail_msg "$http" "$curl_exit"
    printf '%s' "${http:-000}"
    return 1
  done
}

# ── Commands ───────────────────────────────────────────────────────────────────

cmd_whoami() {
  require_creds
  local data
  data=$(jget "$JENKINS_URL/api/json?tree=nodeDescription")
  if [[ $? -ne 0 ]]; then
    echo "❌ Authentication failed" >&2
    exit 1
  fi
  local version
  version=$(curl -sI -u "$JENKINS_USER:$JENKINS_API_TOKEN" "$JENKINS_URL/api/json" \
    | grep -i "x-jenkins:" | awk '{print $2}' | tr -d '\r')
  echo "✅ Authenticated as: $JENKINS_USER"
  echo "   Instance: $JENKINS_URL"
  echo "   Jenkins:  v${version:-unknown}"
}

cmd_folders() {
  require_creds
  local data
  data=$(jget "$JENKINS_URL/api/json")
  if [[ $? -ne 0 || -z "$data" ]]; then
    echo "❌ Failed to fetch folders — run 'whoami' to verify auth" >&2
    return 1
  fi
  echo "Top-level jobs:"
  echo ""
  printf "  %-35s %s\n" "NAME" "TYPE"
  printf "  %s\n" "$(printf '─%.0s' {1..55})"
  printf '%s' "$data" | python3 -c "
import json,sys
data=json.load(sys.stdin)
for j in data.get('jobs',[]):
  cls=j.get('_class','').split('.')[-1]
  name=j.get('name','?')
  print(f'  {name:35s} {cls}')
"
}

cmd_jobs() {
  require_creds
  local folder="$1"
  local data
  data=$(jget "$JENKINS_URL/job/$folder/api/json")
  if [[ $? -ne 0 || -z "$data" ]]; then
    echo "❌ Failed to fetch jobs in $folder/ — check the folder name and run 'whoami' to verify auth" >&2
    return 1
  fi
  echo "Jobs in $folder/:"
  echo ""
  printf "  %-45s %s\n" "NAME" "TYPE"
  printf "  %s\n" "$(printf '─%.0s' {1..65})"
  printf '%s' "$data" | python3 -c "
import json,sys
data=json.load(sys.stdin)
for j in data.get('jobs',[]):
  cls=j.get('_class','').split('.')[-1]
  name=j.get('name','?')
  print(f'  {name:45s} {cls}')
"
}

cmd_branches() {
  require_creds
  local folder="$1" project="$2"
  local data
  data=$(jget "$JENKINS_URL/job/$folder/job/$project/api/json")
  if [[ $? -ne 0 || -z "$data" ]]; then
    echo "❌ Failed to fetch branches in $folder/$project — check the folder/project names and run 'whoami' to verify auth" >&2
    return 1
  fi
  echo "Branches in $folder/$project:"
  echo ""
  printf '%s' "$data" | python3 -c "
import json,sys
data=json.load(sys.stdin)
for j in data.get('jobs',[]):
  color=j.get('color','?')
  name=j.get('name','?')
  sym={'blue':'✅','red':'❌','yellow':'⚠️','notbuilt':'⚪','disabled':'🚫','aborted':'⏹️'}.get(color.replace('_anime',''),'❓')
  building=' (building)' if '_anime' in color else ''
  print(f'  {sym} {name:40s} {color}{building}')
"
}

cmd_status() {
  require_creds
  local folder="$1" project="$2" branch="${3:-development}"
  branch="${branch//\//%2F}"
  local base="$JENKINS_URL/job/$folder/job/$project/job/$branch"
  local data
  data=$(jget "$base/lastBuild/api/json?tree=number,result,timestamp,duration,displayName,building")
  if [[ $? -ne 0 || -z "$data" ]]; then
    echo "❌ No builds found for $folder/$project/$branch"
    return 1
  fi
  printf '%s' "$data" | J_LABEL="$folder/$project/$branch" J_BASE="$base" python3 -c "
import json,sys,os
from datetime import datetime
d=json.load(sys.stdin)
ts=datetime.fromtimestamp(d['timestamp']/1000).strftime('%Y-%m-%d %H:%M:%S')
dur=d['duration']/1000
mins=int(dur//60)
secs=int(dur%60)
result=d.get('result','IN PROGRESS') if not d.get('building') else 'BUILDING'
sym={'SUCCESS':'✅','FAILURE':'❌','UNSTABLE':'⚠️','ABORTED':'⏹️','BUILDING':'🔄','IN PROGRESS':'🔄'}.get(result,'❓')
print(f'{sym} {result}')
print(f'   Build:    {d[\"displayName\"]} (#{d[\"number\"]})')
print(f'   Branch:   {os.environ[\"J_LABEL\"]}')
print(f'   Duration: {mins}m {secs}s')
print(f'   When:     {ts}')
print(f'   URL:      {os.environ[\"J_BASE\"]}/{d[\"number\"]}/')
"
}

cmd_log() {
  require_creds
  local folder="$1" project="$2" branch="${3:-development}" build="${4:-lastBuild}"
  branch="${branch//\//%2F}"
  local url="$JENKINS_URL/job/$folder/job/$project/job/$branch/$build/consoleText"
  local output
  output=$(jget "$url" 120)
  if [[ $? -ne 0 || -z "$output" ]]; then
    echo "❌ No console output for $folder/$project/$branch #$build" >&2
    return 1
  fi
  echo "$output"
}

cmd_log_tail() {
  require_creds
  local folder="$1" project="$2" branch="${3:-development}" lines="${4:-80}"
  branch="${branch//\//%2F}"
  local url="$JENKINS_URL/job/$folder/job/$project/job/$branch/lastBuild/consoleText"
  local output
  output=$(jget "$url" 120)
  if [[ $? -ne 0 || -z "$output" ]]; then
    echo "❌ No console output for $folder/$project/$branch" >&2
    return 1
  fi
  echo "$output" | tail -n "$lines"
}

cmd_build() {
  require_creds
  local folder="$1" project="$2" branch="${3:-development}"
  branch="${branch//\//%2F}"
  local url="$JENKINS_URL/job/$folder/job/$project/job/$branch/build"
  local code
  code=$(jpost "$url")
  if [[ "$code" == "201" ]]; then
    echo "✅ Build triggered: $folder/$project/$branch"
    echo "   URL: $JENKINS_URL/job/$folder/job/$project/job/$branch/"
  else
    echo "❌ Failed to trigger build (HTTP $code)" >&2
    echo "   URL: $url" >&2
    return 1
  fi
}

cmd_build_with_params() {
  if [[ $# -lt 3 ]]; then
    cat >&2 <<'USAGE'
Usage: jenkins-api.sh build-with-params <folder> <project> [branch] KEY=value [KEY=value ...]

Triggers a parameterized build via /buildWithParameters.
Branch defaults to "development". Each KEY=value pair is form-encoded and
POSTed as a build parameter. To discover a job's parameters, see
workflows/build-params.md.

Example:
  jenkins-api.sh build-with-params backend my-service master DEPLOY_ENV=staging RUN_TESTS=true
USAGE
    return 1
  fi
  require_creds
  local folder="$1" project="$2"
  shift 2
  local branch="development"
  if [[ $# -gt 0 && "$1" != *=* ]]; then
    branch="$1"
    shift
  fi
  branch="${branch//\//%2F}"
  if [[ $# -eq 0 ]]; then
    echo "❌ build-with-params requires at least one KEY=value pair" >&2
    return 1
  fi
  local form_args=()
  local pair key value
  for pair in "$@"; do
    if [[ "$pair" != *=* ]]; then
      echo "❌ Invalid parameter (expected KEY=value): $pair" >&2
      return 1
    fi
    key="${pair%%=*}"
    value="${pair#*=}"
    if [[ -z "$key" ]]; then
      echo "❌ Empty parameter name in: $pair" >&2
      return 1
    fi
    form_args+=(--data-urlencode "$key=$value")
  done
  local url="$JENKINS_URL/job/$folder/job/$project/job/$branch/buildWithParameters"
  local code
  code=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
    -u "$JENKINS_USER:$JENKINS_API_TOKEN" \
    "${form_args[@]}" \
    "$url")
  if [[ "$code" == "201" ]]; then
    echo "✅ Parameterized build triggered: $folder/$project/$branch"
    echo "   Params: $*"
    echo "   URL: $JENKINS_URL/job/$folder/job/$project/job/$branch/"
  else
    echo "❌ Failed to trigger parameterized build (HTTP $code)" >&2
    echo "   URL: $url" >&2
    echo "   Params: $*" >&2
    return 1
  fi
}

cmd_health() {
  require_creds
  local folder="$1"
  local data
  data=$(jget "$JENKINS_URL/job/$folder/api/json")
  if [[ $? -ne 0 || -z "$data" ]]; then
    echo "❌ Failed to fetch $folder/ — check the folder name and run 'whoami' to verify auth" >&2
    return 1
  fi
  echo "Health: $folder/"
  echo ""
  printf "  %-40s %-12s %-12s %s\n" "PROJECT" "BRANCH" "RESULT" "AGE"
  printf "  %s\n" "$(printf '─%.0s' {1..80})"

  printf '%s' "$data" | J_FOLDER="$folder" JENKINS_URL="$JENKINS_URL" JENKINS_USER="$JENKINS_USER" JENKINS_API_TOKEN="$JENKINS_API_TOKEN" python3 -c "
import json,sys,urllib.request,urllib.error,base64,os
from datetime import datetime

data=json.load(sys.stdin)
url=os.environ['JENKINS_URL']
user=os.environ['JENKINS_USER']
token=os.environ['JENKINS_API_TOKEN']
creds=base64.b64encode(f'{user}:{token}'.encode()).decode()
folder=os.environ['J_FOLDER']
FALLBACK_BRANCHES=['development','master']

def discover_branches(project):
  burl=f'{url}/job/{folder}/job/{project}/api/json?tree=jobs[name]'
  req=urllib.request.Request(burl)
  req.add_header('Authorization',f'Basic {creds}')
  with urllib.request.urlopen(req,timeout=5) as resp:
    pd=json.loads(resp.read())
  names=[b.get('name') for b in pd.get('jobs',[]) if b.get('name')]
  if not names:
    raise ValueError('no branches returned')
  return names

for j in data.get('jobs',[]):
  name=j['name']
  cls=j.get('_class','')
  if 'MultiBranch' not in cls:
    continue
  try:
    branches=discover_branches(name)
  except Exception as e:
    print(f'WARNING: branch discovery failed for {folder}/{name} ({e}); falling back to {FALLBACK_BRANCHES}',file=sys.stderr)
    branches=FALLBACK_BRANCHES
  for branch in branches:
    burl=f'{url}/job/{folder}/job/{name}/job/{branch}/lastBuild/api/json?tree=result,timestamp'
    req=urllib.request.Request(burl)
    req.add_header('Authorization',f'Basic {creds}')
    try:
      with urllib.request.urlopen(req,timeout=5) as resp:
        bd=json.loads(resp.read())
        result=bd.get('result','?')
        ts=datetime.fromtimestamp(bd['timestamp']/1000)
        age=(datetime.now()-ts).days
        age_str=f'{age}d ago' if age>0 else 'today'
        sym={'SUCCESS':'✅','FAILURE':'❌','UNSTABLE':'⚠️','ABORTED':'⏹️'}.get(result,'❓')
        print(f'  {name:40s} {branch:12s} {sym} {result:10s} {age_str}')
    except urllib.error.HTTPError as e:
      if e.code!=404:
        print(f'WARNING: {folder}/{name}/{branch} lastBuild fetch failed (HTTP {e.code})',file=sys.stderr)
    except Exception as e:
      print(f'WARNING: {folder}/{name}/{branch} lastBuild fetch failed ({e})',file=sys.stderr)
"
}

# ── Main ───────────────────────────────────────────────────────────────────────

cmd="${1:-}"
shift 2>/dev/null || true

case "$cmd" in
  whoami)    cmd_whoami ;;
  folders)   cmd_folders ;;
  jobs)      cmd_jobs "$@" ;;
  branches)  cmd_branches "$@" ;;
  status)    cmd_status "$@" ;;
  log)       cmd_log "$@" ;;
  log-tail)  cmd_log_tail "$@" ;;
  build)     cmd_build "$@" ;;
  build-with-params) cmd_build_with_params "$@" ;;
  health)    cmd_health "$@" ;;
  *)
    echo "Unknown command: ${cmd:-(none)}" >&2
    echo "Commands: whoami | folders | jobs | branches | status | log | log-tail | build | build-with-params | health" >&2
    exit 1
    ;;
esac
