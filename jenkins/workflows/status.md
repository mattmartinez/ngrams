# Status Workflow

## When to Use
User wants to check the build status of a project or branch.

## Steps

1. **Identify the target.** The user will name a service, project, or repo.
   Resolve it to `<folder>/<project>/<branch>` using knowledge of the instance layout.
   If unsure which folder, run `folders` first, then `jobs <folder>` to locate it.

2. **Check status:**
```bash
source ~/.gsd/jenkins.env
bash <skill_dir>/scripts/jenkins-api.sh status <folder> <project> [branch]
```
Branch defaults to `development` if not specified.

3. **Report.** Show the result, build number, duration, and age.
   If the build failed, offer to pull logs with the diagnose workflow.

## Checking Multiple Projects

To see all projects in a folder at once:
```bash
bash <skill_dir>/scripts/jenkins-api.sh health <folder>
```

This shows the last build result for `development` and `master` branches
of every multibranch project in the folder.

## Checking All Branches

To see which branches exist and their status:
```bash
bash <skill_dir>/scripts/jenkins-api.sh branches <folder> <project>
```
