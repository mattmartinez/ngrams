# Diagnose Workflow

## When to Use
User wants to understand why a build failed, or wants to see build logs.

## Steps

1. **Identify the target.** Resolve to `<folder>/<project>/<branch>`.

2. **Check current status first:**
```bash
source ~/.gsd/jenkins.env
bash <skill_dir>/scripts/jenkins-api.sh status <folder> <project> [branch]
```

3. **Pull the log tail** (last 80 lines covers most failure output):
```bash
bash <skill_dir>/scripts/jenkins-api.sh log-tail <folder> <project> [branch] [lines]
```

4. **Analyze the output.** Look for:
   - Compilation errors (Java: `error:`, Gradle: `FAILED`, Node: `ERR!`)
   - Test failures (`FAILED`, `AssertionError`, `expected ... but was`)
   - Checkstyle/CodeNarc violations (`maxWarnings=0` enforcement)
   - Infrastructure issues (`Cannot connect`, `timeout`, `OOMKilled`)
   - Credential/permission errors (`401`, `403`, `Access Denied`)

5. **Report findings.** Summarize:
   - What failed (stage name, error message)
   - Root cause if identifiable
   - Suggested fix

## Full Log

If the tail doesn't have enough context, pull the full log:
```bash
bash <skill_dir>/scripts/jenkins-api.sh log <folder> <project> [branch] [buildNumber]
```

The full log can be large (10K+ lines for complex pipelines). Pipe through
`grep -i 'error\|fail\|exception'` to filter, or use `read` tool on the
output to page through it.

## Specific Build Number

To check a specific build (not just the latest):
```bash
bash <skill_dir>/scripts/jenkins-api.sh log <folder> <project> <branch> 42
```
