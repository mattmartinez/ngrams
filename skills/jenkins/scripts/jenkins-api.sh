#!/usr/bin/env bash
# jenkins-api.sh — Thin Jenkins REST client for agent use.
#
# Reads credentials from env vars (source ~/.gsd/jenkins.env before calling).
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
#   health <folder>                    Health summary for all multibranch jobs in a folder
#
# Usage:
#   source ~/.gsd/jenkins.env
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
    echo "Run: source ~/.gsd/jenkins.env" >&2
    exit 1
  fi
}

# ── HTTP helpers ───────────────────────────────────────────────────────────────

jget() {
  curl -sf -u "$JENKINS_USER:$JENKINS_API_TOKEN" "$1"
}

jpost() {
  curl -s -o /dev/null -w "%{http_code}" -X POST \
    -u "$JENKINS_USER:$JENKINS_API_TOKEN" "$1"
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
  echo "Top-level jobs:"
  echo ""
  printf "  %-35s %s\n" "NAME" "TYPE"
  printf "  %s\n" "$(printf '─%.0s' {1..55})"
  jget "$JENKINS_URL/api/json" | python3 -c "
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
  echo "Jobs in $folder/:"
  echo ""
  printf "  %-45s %s\n" "NAME" "TYPE"
  printf "  %s\n" "$(printf '─%.0s' {1..65})"
  jget "$JENKINS_URL/job/$folder/api/json" | python3 -c "
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
  echo "Branches in $folder/$project:"
  echo ""
  jget "$JENKINS_URL/job/$folder/job/$project/api/json" | python3 -c "
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
  local base="$JENKINS_URL/job/$folder/job/$project/job/$branch"
  local data
  data=$(jget "$base/lastBuild/api/json?tree=number,result,timestamp,duration,displayName,building")
  if [[ $? -ne 0 || -z "$data" ]]; then
    echo "❌ No builds found for $folder/$project/$branch"
    return 1
  fi
  python3 -c "
import json,sys
from datetime import datetime
d=json.loads('''$data''')
ts=datetime.fromtimestamp(d['timestamp']/1000).strftime('%Y-%m-%d %H:%M:%S')
dur=d['duration']/1000
mins=int(dur//60)
secs=int(dur%60)
result=d.get('result','IN PROGRESS') if not d.get('building') else 'BUILDING'
sym={'SUCCESS':'✅','FAILURE':'❌','UNSTABLE':'⚠️','ABORTED':'⏹️','BUILDING':'🔄','IN PROGRESS':'🔄'}.get(result,'❓')
print(f'{sym} {result}')
print(f'   Build:    {d[\"displayName\"]} (#{d[\"number\"]})')
print(f'   Branch:   $folder/$project/$branch')
print(f'   Duration: {mins}m {secs}s')
print(f'   When:     {ts}')
print(f'   URL:      $base/{d[\"number\"]}/')
"
}

cmd_log() {
  require_creds
  local folder="$1" project="$2" branch="${3:-development}" build="${4:-lastBuild}"
  local url="$JENKINS_URL/job/$folder/job/$project/job/$branch/$build/consoleText"
  local output
  output=$(jget "$url")
  if [[ $? -ne 0 || -z "$output" ]]; then
    echo "❌ No console output for $folder/$project/$branch #$build" >&2
    return 1
  fi
  echo "$output"
}

cmd_log_tail() {
  require_creds
  local folder="$1" project="$2" branch="${3:-development}" lines="${4:-80}"
  local url="$JENKINS_URL/job/$folder/job/$project/job/$branch/lastBuild/consoleText"
  local output
  output=$(jget "$url")
  if [[ $? -ne 0 || -z "$output" ]]; then
    echo "❌ No console output for $folder/$project/$branch" >&2
    return 1
  fi
  echo "$output" | tail -n "$lines"
}

cmd_build() {
  require_creds
  local folder="$1" project="$2" branch="${3:-development}"
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

cmd_health() {
  require_creds
  local folder="$1"
  echo "Health: $folder/"
  echo ""
  printf "  %-40s %-12s %-12s %s\n" "PROJECT" "BRANCH" "RESULT" "AGE"
  printf "  %s\n" "$(printf '─%.0s' {1..80})"

  jget "$JENKINS_URL/job/$folder/api/json" | python3 -c "
import json,sys,urllib.request,base64,os
from datetime import datetime

data=json.load(sys.stdin)
url=os.environ['JENKINS_URL']
user=os.environ['JENKINS_USER']
token=os.environ['JENKINS_API_TOKEN']
creds=base64.b64encode(f'{user}:{token}'.encode()).decode()
folder='$folder'

for j in data.get('jobs',[]):
  name=j['name']
  cls=j.get('_class','')
  if 'MultiBranch' not in cls:
    continue
  for branch in ['development','master']:
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
    except:
      pass
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
  health)    cmd_health "$@" ;;
  *)
    echo "Unknown command: ${cmd:-(none)}" >&2
    echo "Commands: whoami | folders | jobs | branches | status | log | log-tail | build | health" >&2
    exit 1
    ;;
esac
