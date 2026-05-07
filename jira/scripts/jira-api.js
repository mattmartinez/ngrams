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
 *   bulk-update [options]         Apply field changes to every issue matching a JQL
 *
 * Create options:
 *   --project KEY                 Jira project key (required)
 *   --summary "text"              Issue summary (required)
 *   --type Bug|Story|Task|Spike   Issue type (default: Task)
 *   --priority High|Medium|Low    Priority (default: Medium)
 *   --labels "a,b,c"              Comma-separated labels
 *   --description "text"          Plain text description (converted to ADF)
 *   --description-file path.json  Pre-built ADF JSON file (preferred)
 *   --parent KEY                  Parent issue key (e.g. Epic key for a Story)
 *
 * Search options:
 *   --jql "JQL query"             JQL string (required)
 *   --max N                       Max results (default: 20)
 *
 * Bulk-update options (run with --help for full details):
 *   --jql "JQL query"             Selects which issues to update (required)
 *   --assignee ACCOUNT_ID         Reassign matched issues
 *   --status NAME                 Transition matched issues
 *   --labels "a,b,c"              Replace labels (empty string clears)
 *   --priority NAME               Set priority
 *   --apply                       Actually perform the updates (default: dry run)
 *
 * Usage:
 *   source ~/.gsd/jira.env
 *   node jira-api.js whoami
 *   node jira-api.js create --project CD --summary "Fix thing" --type Bug
 *   node jira-api.js search --jql 'project = CD AND statusCategory != Done'
 *   node jira-api.js bulk-update --jql 'project = CD AND labels = stale' --labels ''
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

// ADF block-level node types allowed as listItem children.
const BLOCK_NODE_TYPES = new Set([
  'paragraph', 'heading', 'codeBlock', 'bulletList', 'orderedList',
  'blockquote', 'rule', 'panel', 'table', 'mediaGroup', 'mediaSingle',
]);

function isBlockNode(n) {
  return n && typeof n === 'object' && BLOCK_NODE_TYPES.has(n.type);
}

function isAdfNode(n) {
  return n && typeof n === 'object' && typeof n.type === 'string';
}

// Strings or non-block ADF nodes are inline-like and must be wrapped in a paragraph.
function isInlineLike(n) {
  if (typeof n === 'string') return true;
  return isAdfNode(n) && !isBlockNode(n);
}

// Coerce any list-item input (string, inline node, block node, or array of
// inlines/blocks) into a valid listItem.content array of block nodes.
// ADF requires listItem children to be block-level — bare inline nodes (text,
// bold, code) will cause Jira to reject the entire document with HTTP 400.
function normalizeListItem(item) {
  if (typeof item === 'string') {
    return [paragraph(text(item))];
  }
  if (Array.isArray(item)) {
    if (item.length === 0) return [paragraph(text(''))];
    if (item.every(isBlockNode)) return item;
    if (item.every(isInlineLike)) return [paragraph(...item)];
    // Mixed: partition contiguous inline runs into paragraphs, pass blocks as siblings.
    const out = [];
    let run = [];
    const flushRun = () => {
      if (run.length) { out.push(paragraph(...run)); run = []; }
    };
    for (const n of item) {
      if (isBlockNode(n)) { flushRun(); out.push(n); }
      else { run.push(n); }
    }
    flushRun();
    return out;
  }
  if (isBlockNode(item)) return [item];
  if (isAdfNode(item)) return [paragraph(item)];          // inline node
  return [paragraph(text(String(item)))];                 // fallback
}

function bulletList(...items) {
  return {
    type: 'bulletList',
    content: items.map(item => ({ type: 'listItem', content: normalizeListItem(item) })),
  };
}

function orderedList(...items) {
  return {
    type: 'orderedList',
    content: items.map(item => ({ type: 'listItem', content: normalizeListItem(item) })),
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
  const parentKey   = get('--parent');

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
      ...(parentKey ? { parent: { key: parentKey } } : {}),
    },
  };

  const result = await request('POST', '/issue', payload);
  console.log(`✅ Created ${result.key}: ${summary}`);
  console.log(`   ${BASE_URL}/browse/${result.key}`);
}

// Narrow JQL pre-validator — catches obvious foot-guns before hitting the API.
// Returns an error string on failure, or null when the query is plausibly valid.
// Not a full parser: only checks unmatched double quotes and bare leading/trailing AND/OR.
function validateJql(jql) {
  const trimmed = jql.trim();
  if (!trimmed) return 'empty query';

  let quotes = 0;
  for (let i = 0; i < jql.length; i++) {
    if (jql[i] === '"' && jql[i - 1] !== '\\') quotes++;
  }
  if (quotes % 2 !== 0) return 'unmatched double quote';

  if (/^(AND|OR)\b/i.test(trimmed)) return 'leading AND/OR with no left operand';
  if (/\b(AND|OR)$/i.test(trimmed)) return 'trailing AND/OR with no right operand';

  return null;
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

  const jqlError = validateJql(jql);
  if (jqlError) {
    console.error(`Malformed JQL: ${jqlError}`);
    process.exit(1);
  }

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

function printBulkUpdateHelp() {
  console.log(`Usage: jira-api.js bulk-update --jql 'JQL' [options]

Apply the same field changes to every issue matching the JQL.
Without --apply, runs as a dry run: prints matched issues + planned changes
and exits without writing anything.

Options:
  --jql 'JQL'         JQL selecting issues to update (required)
  --assignee ID       Set assignee accountId on every matched issue
  --status NAME       Transition every matched issue to this status (case-insensitive)
  --labels "a,b,c"    REPLACE labels on every matched issue (empty string clears)
  --priority NAME     Set priority (Highest | High | Medium | Low | Lowest)
  --max N             Max matches to fetch (default 20)
  --apply             Actually perform the updates (omit for dry run)
  --help              Show this help

At least one of --assignee, --status, --labels, --priority must be provided.

Notes:
  --assignee takes an accountId, not an email or display name.
  --labels REPLACES the label set; pass --labels "" to clear all labels.
  --status looks up matching transitions per-issue and may legitimately fail
  on individual issues whose current state does not allow that transition.
  Failures are reported per-issue; the command exits non-zero if any failed.

Examples:
  bulk-update --jql 'project = CD AND labels = stale' --labels ''
  bulk-update --jql 'project = CD AND priority = Lowest' --priority Low --apply
`);
}

async function cmdBulkUpdate(args) {
  if (args.includes('--help') || args.includes('-h')) {
    printBulkUpdateHelp();
    return;
  }

  requireCreds();

  const get = (flag) => {
    const i = args.indexOf(flag);
    return i !== -1 ? args[i + 1] : null;
  };
  const has = (flag) => args.includes(flag);

  const jql       = get('--jql');
  const assignee  = get('--assignee');
  const status    = get('--status');
  const labelsRaw = get('--labels');
  const priority  = get('--priority');
  const max       = parseInt(get('--max') || '20', 10);
  const apply     = has('--apply');

  if (!jql) { console.error('--jql is required'); process.exit(1); }

  const jqlError = validateJql(jql);
  if (jqlError) {
    console.error(`Malformed JQL: ${jqlError}`);
    process.exit(1);
  }

  // Distinguish "flag absent" (null) from "flag present with empty value" so an
  // empty --labels means "clear all labels" rather than "no change".
  const labels = labelsRaw === null
    ? null
    : (labelsRaw ? labelsRaw.split(',').map(l => l.trim()).filter(Boolean) : []);

  if (assignee == null && status == null && labels == null && priority == null) {
    console.error('At least one of --assignee, --status, --labels, --priority is required');
    process.exit(1);
  }

  const data = await request(
    'POST',
    '/search/jql',
    { jql, maxResults: max, fields: ['summary', 'status', 'priority', 'assignee', 'labels'] }
  );
  const issues = data.issues || [];

  if (!issues.length) {
    console.log('No issues match the JQL — nothing to update.');
    console.log(`JQL: ${jql}`);
    return;
  }

  console.log(`Matched ${issues.length} issue(s):`);
  for (const issue of issues) {
    const key = issue.key.padEnd(10);
    const summary = (issue.fields.summary || '').slice(0, 60);
    console.log(`  ${key} ${summary}`);
  }
  console.log('');
  console.log('Planned changes:');
  if (assignee != null) console.log(`  assignee → ${assignee}`);
  if (status   != null) console.log(`  status   → ${status}`);
  if (labels   != null) console.log(`  labels   → ${labels.length ? labels.join(', ') : '(cleared)'}`);
  if (priority != null) console.log(`  priority → ${priority}`);
  console.log('');

  if (!apply) {
    console.log('Dry run — pass --apply to perform these updates.');
    return;
  }

  let ok = 0, failed = 0;
  for (const issue of issues) {
    try {
      const fields = {};
      if (labels   !== null) fields.labels   = labels;
      if (priority !== null) fields.priority = { name: priority };

      if (assignee !== null) {
        await request('PUT', `/issue/${issue.key}/assignee`, { accountId: assignee });
      }
      if (Object.keys(fields).length) {
        await request('PUT', `/issue/${issue.key}`, { fields });
      }
      if (status !== null) {
        const transitions = await request('GET', `/issue/${issue.key}/transitions`);
        const want = status.toLowerCase();
        const t = (transitions.transitions || []).find(t =>
          t.name.toLowerCase() === want ||
          (t.to && t.to.name && t.to.name.toLowerCase() === want)
        );
        if (!t) {
          throw new Error(`no transition to "${status}" available from current state`);
        }
        await request('POST', `/issue/${issue.key}/transitions`, { transition: { id: t.id } });
      }
      console.log(`✅ ${issue.key} updated`);
      ok++;
    } catch (err) {
      console.error(`❌ ${issue.key} failed: ${err.message}`);
      failed++;
    }
  }
  console.log('');
  console.log(`Done. ${ok} updated, ${failed} failed.`);
  if (failed) process.exit(1);
}

// ── Main ──────────────────────────────────────────────────────────────────────

if (require.main === module) {
  const [,, command, ...rest] = process.argv;

  (async () => {
    switch (command) {
      case 'whoami':         await cmdWhoami();           break;
      case 'list-projects':  await cmdListProjects();     break;
      case 'create':         await cmdCreate(rest);       break;
      case 'search':         await cmdSearch(rest);       break;
      case 'bulk-update':    await cmdBulkUpdate(rest);   break;
      default:
        console.error(`Unknown command: ${command || '(none)'}`);
        console.error('Commands: whoami | list-projects | create | search | bulk-update');
        process.exit(1);
    }
  })().catch(err => {
    console.error(`Error: ${err.message}`);
    process.exit(1);
  });
}

// Export ADF helpers for use in description-building scripts
module.exports = { adf, heading, paragraph, text, bold, code, codeBlock, bulletList, orderedList, rule, textToAdf };
