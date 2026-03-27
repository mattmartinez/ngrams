# Setup Workflow

## When to Use
Credentials file `~/.gsd/jenkins.env` does not exist, or auth fails.

## Steps

1. **Collect credentials** using `secure_env_collect`:

```
Keys:
  JENKINS_URL        — The Jenkins server URL (e.g. https://jenkins.example.com)
  JENKINS_USER       — Your Jenkins username (matches your login identity)
  JENKINS_API_TOKEN  — Generate in Jenkins: Your Name → Configure → API Token → Add new Token

Destination: dotenv
Path: ~/.gsd/jenkins.env
```

Guidance for JENKINS_API_TOKEN:
- Log into your Jenkins instance
- Click your username (top right) → Configure
- Scroll to "API Token" section
- Click "Add new Token", name it (e.g. `gsd-jenkins-skill`)
- Click "Generate" and copy the value (shown only once)

2. **Verify** by running:
```bash
source ~/.gsd/jenkins.env
bash <skill_dir>/scripts/jenkins-api.sh whoami
```

3. If `whoami` returns ✅, setup is complete.
