# List Workflow

## When to Use
User wants to see what's on Jenkins — folders, jobs, or branches.

## Steps

### Top-level folders
```bash
source ~/.gsd/jenkins.env
bash <skill_dir>/scripts/jenkins-api.sh folders
```

### Jobs in a folder
```bash
bash <skill_dir>/scripts/jenkins-api.sh jobs <folder>
```

### Branches in a multibranch project
```bash
bash <skill_dir>/scripts/jenkins-api.sh branches <folder> <project>
```

## Navigating the Hierarchy

Jenkins uses nested folders. The typical structure is:

```
Jenkins root
├── folder-a/               (Folder)
│   ├── project-1/          (WorkflowMultiBranchProject)
│   │   ├── master          (WorkflowJob — branch)
│   │   ├── development     (WorkflowJob — branch)
│   │   └── TICKET-123      (WorkflowJob — feature branch)
│   └── project-2/
├── folder-b/
│   └── sub-folder/         (Folders can nest)
│       └── project-3/
└── standalone-job           (WorkflowJob — no folder)
```

Use `folders` → `jobs` → `branches` to drill down. Each level adds a
`/job/{name}` segment to the URL path.
