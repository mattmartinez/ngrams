#!/usr/bin/env node
/**
 * jira-api.js — Thin Jira Cloud REST client for agent use.
 *
 * Reads credentials from env vars (source ~/.gsd/jira.env before calling).
 *
 * Commands:
 *   whoami                        Verify auth, print current user
 *   list-projects                 List all accessible projects
 *   create [options]              Create an issue
 *   search [options]              Search with JQL
 *
 * Create options:
 *   --project KEY                 Jira project key (required)
 *   --summary "text"              Issue summary (required)
 *   --type Bug|Story|Task|Spike   Issue type (default: Task)
 *   --priority High|Medium|Low    Priority (default: Medium)
 *   --labels "a,b,c"              Comma-separated labels
 *   --description "text"          Plain text description (converted to ADF)
 *   --description-file path.json  Pre-built ADF JSON file (preferred)
 *
 * Search options:
 *   --jql "JQL query"             JQL string (required)
 *   --max N                       Max results (default: 20)
 *
 * Usage:
 *   source ~/.gsd/jira.env
 *   node jira-api.js whoami
 *   node jira-api.js create --project CD --summary "Fix thing" --type Bug
 *   node jira-api.js search --jql 'project = CD AND statusCategory != Done'
 */

'use strict';

const fs = require('fs');
const path = require('path');

// ── Credentials ───────────────────────────────────────────────────────────────

const BASE_URL = (process.env.JIRA_BASE_URL || '').replace(/\/$/, '');
const EMAIL    = process.env.JIRA_EMAIL;
const TOKEN    = process.env.JIRA_API_TOKEN;

function requireCreds() {
  const missing = [];
  if (!BASE_URL) missing.push('JIRA_BASE_URL');
  if (!EMAIL)    missing.push('JIRA_EMAIL');
  if (!TOKEN)    missing.push('JIRA_API_TOKEN');
  if (missing.length) {
    console.error(`Missing credentials: ${missing.join(', ')}`);
    console.error('Run: source ~/.gsd/jira.env');
    process.exit(1);
  }
}

function authHeader() {
  return 'Basic ' + Buffer.from(`${EMAIL}:${TOKEN}`).toString('base64');
}

// ── HTTP ──────────────────────────────────────────────────────────────────────

async function request(method, endpoint, body) {
  const url = `${BASE_URL}/rest/api/3${endpoint}`;
  const opts = {
    method,
    headers: {
      'Authorization': authHeader(),
      'Content-Type':  'application/json',
      'Accept':        'application/json',
    },
  };
  if (body) opts.body = JSON.stringify(body);

  const res = await fetch(url, opts);
  const text = await res.text();

  if (!res.ok) {
    let msg = text;
    try {
      const parsed = JSON.parse(text);
      // Jira wraps errors in { errorMessages: [], errors: {} }
      const errs = [
        ...(parsed.errorMessages || []),
        ...Object.values(parsed.errors || {}),
      ];
      if (errs.length) msg = errs.join('; ');
    } catch (_) {}
    throw new Error(`HTTP ${res.status}: ${msg}`);
  }

  return text ? JSON.parse(text) : null;
}

// ── ADF helpers ───────────────────────────────────────────────────────────────
// These are also exported so the agent can build descriptions programmatically.

function adf(blocks) {
  return { version: 1, type: 'doc', content: blocks };
}

function heading(t, level = 2) {
  return { type: 'heading', attrs: { level }, content: [{ type: 'text', text: t }] };
}

function paragraph(...inlines) {
  const content = inlines.map(i => typeof i === 'string' ? text(i) : i);
  return { type: 'paragraph', content };
}

function text(t) {
  return { type: 'text', text: String(t) };
}

function bold(t) {
  return { type: 'text', text: String(t), marks: [{ type: 'strong' }] };
}

function code(t) {
  return { type: 'text', text: String(t), marks: [{ type: 'code' }] };
}

function codeBlock(content, language = null) {
  return {
    type: 'codeBlock',
    attrs: language ? { language } : {},
    content: [{ type: 'text', text: content }],
  };
}

function bulletList(...items) {
  return {
    type: 'bulletList',
    content: items.map(item => ({
      type: 'listItem',
      content: Array.isArray(item) ? item : [paragraph(text(typeof item === 'string' ? item : String(item)))],
    })),
  };
}

function orderedList(...items) {
  return {
    type: 'orderedList',
    content: items.map(item => ({
      type: 'listItem',
      content: Array.isArray(item) ? item : [paragraph(text(typeof item === 'string' ? item : String(item)))],
    })),
  };
}

function rule() {
  return { type: 'rule' };
}

/** Convert a plain string to minimal ADF (one paragraph per non-empty line). */
function textToAdf(str) {
  const blocks = str
    .split('\n')
    .map(l => l.trim())
    .filter(l => l.length > 0)
    .map(l => paragraph(text(l)));
  return adf(blocks.length ? blocks : [paragraph(text(str))]);
}

// ── Commands ──────────────────────────────────────────────────────────────────

async function cmdWhoami() {
  requireCreds();
  const user = await request('GET', '/myself');
  console.log(`✅ Authenticated as: ${user.displayName} (${user.emailAddress})`);
  console.log(`   Account ID: ${user.accountId}`);
  console.log(`   Instance:   ${BASE_URL}`);
}

async function cmdListProjects() {
  requireCreds();
  const data = await request('GET', '/project/search?maxResults=100&orderBy=name');
  const projects = data.values || [];
  if (!projects.length) {
    console.log('No accessible projects found.');
    return;
  }
  console.log(`Found ${projects.length} projects:\n`);
  console.log('KEY       NAME');
  console.log('─'.repeat(60));
  for (const p of projects) {
    console.log(`${p.key.padEnd(10)}${p.name}`);
  }
}

async function cmdCreate(args) {
  requireCreds();

  // Parse flags
  const get = (flag) => {
    const i = args.indexOf(flag);
    return i !== -1 ? args[i + 1] : null;
  };

  const projectKey  = get('--project');
  const summary     = get('--summary');
  const issueType   = get('--type')     || 'Task';
  const priority    = get('--priority') || 'Medium';
  const labelsRaw   = get('--labels')   || '';
  const descText    = get('--description');
  const descFile    = get('--description-file');

  if (!projectKey) { console.error('--project is required'); process.exit(1); }
  if (!summary)    { console.error('--summary is required');  process.exit(1); }

  // Build description ADF
  let description;
  if (descFile) {
    const raw = fs.readFileSync(descFile, 'utf8');
    description = JSON.parse(raw);
  } else if (descText) {
    description = textToAdf(descText);
  } else {
    description = adf([paragraph(text(''))]);
  }

  const labels = labelsRaw ? labelsRaw.split(',').map(l => l.trim()).filter(Boolean) : [];

  const payload = {
    fields: {
      project:     { key: projectKey },
      summary,
      description,
      issuetype:   { name: issueType },
      priority:    { name: priority },
      ...(labels.length ? { labels } : {}),
    },
  };

  const result = await request('POST', '/issue', payload);
  console.log(`✅ Created ${result.key}: ${summary}`);
  console.log(`   ${BASE_URL}/browse/${result.key}`);
}

async function cmdSearch(args) {
  requireCreds();

  const get = (flag) => {
    const i = args.indexOf(flag);
    return i !== -1 ? args[i + 1] : null;
  };

  const jql = get('--jql');
  const max  = parseInt(get('--max') || '20', 10);

  if (!jql) { console.error('--jql is required'); process.exit(1); }

  const data = await request(
    'POST',
    '/search/jql',
    {
      jql,
      maxResults: max,
      fields: ['summary', 'issuetype', 'priority', 'status', 'assignee'],
    }
  );

  const issues = data.issues || [];
  const total = data.total ?? issues.length;
  if (!issues.length) {
    console.log('No issues found.');
    console.log(`JQL: ${jql}`);
    return;
  }

  console.log(`Found ${total} issue(s) (showing ${issues.length}):\n`);
  console.log('KEY        TYPE     PRI     STATUS                SUMMARY');
  console.log('─'.repeat(90));

  for (const issue of issues) {
    const key      = issue.key.padEnd(10);
    const type     = (issue.fields.issuetype?.name || '').padEnd(8).slice(0, 8);
    const pri      = (issue.fields.priority?.name  || '').padEnd(7).slice(0, 7);
    const status   = (issue.fields.status?.name    || '').padEnd(21).slice(0, 21);
    const summary  = (issue.fields.summary || '').slice(0, 60);
    console.log(`${key} ${type} ${pri} ${status} ${summary}`);
  }

  const encoded = encodeURIComponent(jql);
  console.log(`\n${BASE_URL}/issues/?jql=${encoded}`);
}

// ── Main ──────────────────────────────────────────────────────────────────────

if (require.main === module) {
  const [,, command, ...rest] = process.argv;

  (async () => {
    switch (command) {
      case 'whoami':         await cmdWhoami();          break;
      case 'list-projects':  await cmdListProjects();     break;
      case 'create':         await cmdCreate(rest);       break;
      case 'search':         await cmdSearch(rest);       break;
      default:
        console.error(`Unknown command: ${command || '(none)'}`);
        console.error('Commands: whoami | list-projects | create | search');
        process.exit(1);
    }
  })().catch(err => {
    console.error(`Error: ${err.message}`);
    process.exit(1);
  });
}

// Export ADF helpers for use in description-building scripts
module.exports = { adf, heading, paragraph, text, bold, code, codeBlock, bulletList, orderedList, rule, textToAdf };
