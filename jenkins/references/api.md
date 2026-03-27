# Jenkins API Reference

## Authentication

Jenkins REST API uses HTTP Basic auth: `username:api_token`.

API tokens are generated per-user in Jenkins UI:
**Your Name → Configure → API Token → Add new Token**

API tokens are independent of the auth backend (LDAP, SSO, SAML, local).
Once generated, they authenticate directly against Jenkins — no round-trip
to the identity provider.

On Jenkins 2.129+, API token auth **bypasses CSRF crumb requirements**.
No need to fetch `/crumbIssuer/api/json` first.

## URL Structure

Every Jenkins object has an API endpoint at `{object_url}/api/json`.

For nested folders/multibranch projects:
```
{JENKINS_URL}/job/{folder}/job/{project}/job/{branch}/api/json
```

## Useful Endpoints

| Endpoint | Method | Returns |
|----------|--------|---------|
| `/api/json` | GET | Top-level instance info + job list |
| `/job/{name}/api/json` | GET | Folder or job details |
| `/job/.../lastBuild/api/json` | GET | Most recent build details |
| `/job/.../lastSuccessfulBuild/api/json` | GET | Last green build |
| `/job/.../lastFailedBuild/api/json` | GET | Last red build |
| `/job/.../{number}/api/json` | GET | Specific build details |
| `/job/.../{number}/consoleText` | GET | Raw console output |
| `/job/.../build` | POST | Trigger a build (returns 201) |
| `/job/.../buildWithParameters` | POST | Trigger with params (form data) |
| `/queue/api/json` | GET | Current build queue |

## Filtering with `tree`

The `tree` query parameter filters JSON responses to include only named fields:

```
/api/json?tree=jobs[name,color]
/lastBuild/api/json?tree=number,result,timestamp,duration
```

Nested fields: `tree=jobs[name,lastBuild[result,timestamp]]`

## Build Colors

Multibranch branches expose a `color` field:

| Color | Meaning |
|-------|---------|
| `blue` | Last build succeeded |
| `red` | Last build failed |
| `yellow` | Last build unstable |
| `notbuilt` | Never built |
| `disabled` | Job disabled |
| `aborted` | Last build aborted |
| `*_anime` | Suffix means currently building (e.g. `blue_anime`) |

## Build Results

The `result` field on a build object:

| Result | Meaning |
|--------|---------|
| `SUCCESS` | All stages passed |
| `FAILURE` | A stage failed |
| `UNSTABLE` | Tests failed but build completed |
| `ABORTED` | Build was manually or automatically cancelled |
| `null` | Build is still in progress |
