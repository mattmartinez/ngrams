# Setup Workflow

## When to Use
Credentials file `~/.claude/jenkins.env` does not exist, or auth fails.

## Steps

1. **Generate a JENKINS_API_TOKEN** (do this first — you'll paste it into the file, never into this chat):
   - Log into your Jenkins instance
   - Click your username (top right) → Configure
   - Scroll to "API Token" section
   - Click "Add new Token", name it (e.g. `claude-jenkins-skill`)
   - Click "Generate" and copy the value (shown only once)

2. **Write the credentials file yourself, in your own terminal.** Fill in the
   three blanks and run this — do NOT paste the token into this conversation:

```bash
cat > ~/.claude/jenkins.env <<'EOF'
export JENKINS_URL=https://jenkins.example.com
export JENKINS_USER=your-username
export JENKINS_API_TOKEN=paste-token-here
EOF
chmod 600 ~/.claude/jenkins.env
```

   The `export` on each line is **mandatory**: the skill script runs as a bash
   subprocess after `source ~/.claude/jenkins.env` and reads the values from the
   environment (`$JENKINS_URL` / `os.environ`). Plain `KEY=value` lines never
   reach the subprocess and every command fails the credential check.

3. **Verify** by running:
```bash
source ~/.claude/jenkins.env
bash <skill_dir>/scripts/jenkins-api.sh whoami
```

4. If `whoami` returns ✅, setup is complete.
