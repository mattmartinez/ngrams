---
name: jenkins
description: >
  Interact with Jenkins CI/CD — list jobs, check build status, trigger builds,
  read console output, and diagnose failures. Use when the user says "check
  Jenkins", "build status", "trigger a build", "why did the build fail",
  "Jenkins logs", "run the pipeline", or any request to interact with Jenkins.
  Credentials are stored in ~/.gsd/jenkins.env and work across all projects.
---

<essential_principles>
## Credential Lookup

Credentials live at `~/.gsd/jenkins.env` — outside any repo, available from every terminal.

Load with:
```bash
source ~/.gsd/jenkins.env
# Provides: JENKINS_URL, JENKINS_USER, JENKINS_API_TOKEN
```

If the file does not exist, run: `workflows/setup.md`

The script at `scripts/jenkins-api.sh` handles all Jenkins REST calls. It reads
from env vars — never hardcode credentials anywhere.

## API Patterns

Auth is HTTP Basic with API token. No CSRF crumbs needed for API token auth
on Jenkins 2.129+. API tokens are generated per-user in Jenkins UI and are
independent of the authentication backend (LDAP, SSO, local, etc.).

URL structure for folders/multibranch: `JENKINS_URL/job/{folder}/job/{project}/job/{branch}/...`

Key endpoints:
- List folder jobs: `/job/{folder}/api/json`
- List branches: `/job/{folder}/job/{project}/api/json`
- Last build: `/job/{folder}/job/{project}/job/{branch}/lastBuild/api/json`
- Console output: `/job/{folder}/job/{project}/job/{branch}/{buildNum}/consoleText`
- Trigger build: POST to `/job/{folder}/job/{project}/job/{branch}/build`
- Build with params: POST to `/job/{folder}/job/{project}/job/{branch}/buildWithParameters`

Use `?tree=` parameter to filter JSON fields and reduce payload size.

## Job Types

Jenkins instances commonly use:
- **Folders** (`com.cloudbees.hudson.plugins.folder.Folder`) — organizational containers
- **Multibranch Pipelines** (`WorkflowMultiBranchProject`) — auto-discover branches from SCM
- **Pipeline Jobs** (`WorkflowJob`) — single pipeline, or a branch within a multibranch project
- **Freestyle Jobs** (`FreeStyleProject`) — classic Jenkins jobs

The script handles all of these. Multibranch projects have child branches
(master, development, feature branches). The default branch for status/build
commands is `development` — override with a third argument.
</essential_principles>

<routing>
## Route Based on Intent

| User says | Workflow |
|-----------|----------|
| "check build", "build status", "is it green", "how's the build" | `workflows/status.md` |
| "trigger a build", "run the pipeline", "build it", "deploy" | `workflows/build.md` |
| "why did it fail", "build logs", "console output", "what broke" | `workflows/diagnose.md` |
| "list jobs", "what's on Jenkins", "show pipelines" | `workflows/list.md` |
| "set up Jenkins", credentials missing, env file not found | `workflows/setup.md` |

When intent is clear, go straight to the workflow without asking.
When the user names a specific service or project, resolve it to the right
folder/project path automatically using the instance's folder structure.
</routing>
