# Build Workflow

## When to Use
User wants to trigger a Jenkins build.

## Steps

1. **Identify the target.** Resolve the user's request to `<folder>/<project>/<branch>`.
   Default branch is `development` unless the user specifies otherwise.

2. **Confirm before triggering.** Always confirm with the user before triggering:
   > "Trigger build for `<folder>/<project>/<branch>`?"

   This is an outward-facing action — do not proceed without explicit approval.

3. **Trigger the build:**
```bash
source ~/.gsd/jenkins.env
bash <skill_dir>/scripts/jenkins-api.sh build <folder> <project> [branch]
```

4. **Report.** Show the trigger result and link. If the user wants to wait for
   the result, poll status every 15-30 seconds:
```bash
bash <skill_dir>/scripts/jenkins-api.sh status <folder> <project> [branch]
```
   Stop polling when `result` is no longer `BUILDING` / `IN PROGRESS`.

## Notes

- Jenkins returns HTTP 201 when a build is queued successfully.
- The build may sit in the queue briefly before starting.
- If the job requires parameters, use the Jenkins UI — parameterized builds
  via API need endpoint `/buildWithParameters` with form data, which varies per job.
