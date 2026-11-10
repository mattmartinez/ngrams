<!-- Trimmed excerpt of a real Referee reply, kept for parser tests. The Stats lines describe the full reply, not this excerpt. -->
---
**BUG-1**
- **Hunter's claim:** `search` is pasted into the SQL text (lines 97-98), so any caller can run their own SELECT.
- **Skeptic's response:** ACCEPT (99%). No escaping or binding on the path; reproduced the UNION dump.
- **Your analysis:** Line 97 builds the query with an f-string from the `search` parameter and line 98 executes it. I ran it: a UNION payload returned both users' password hashes in the `email` field, and a lone quote raised `OperationalError` and dropped the connection. No auth stands in front. The Hunter is right.
- **VERDICT: REAL BUG**
- **Confidence:** High
- **True severity:** Critical
- **Suggested fix:** Bind the value: `WHERE username LIKE ?` with `(f"%{search}%",)`.
- **Fix complexity:** Trivial
- **Fix risks:** None expected. `%` and `_` in the search text still act as LIKE wildcards, as they do today.
---
**BUG-2**
- **Hunter's claim:** The download handler joins the raw URL path onto `uploads` with no check (172-175), so files outside `uploads` can be read.
- **Skeptic's response:** ACCEPT (99%). The only gate is the `startswith` test at line 76; reproduced both forms.
- **Your analysis:** Line 172 strips the prefix and line 173 joins the rest onto `uploads` with no normalisation or containment check. I ran it: `/api/files/../secret.txt`, `/api/files/../users.db` and `/api/files//<absolute path>` all returned files from outside `uploads`. The Hunter is right.
- **VERDICT: REAL BUG**
- **Confidence:** High
- **True severity:** Critical
- **Suggested fix:** Resolve the joined path with `os.path.realpath` and reject it unless it stays inside the resolved `uploads` folder.
- **Fix complexity:** Trivial
- **Fix risks:** Low. Symlinks inside `uploads` that point elsewhere stop working, which is the safe outcome. Run the check on the decoded name (see BUG-31).
---
**BUG-3**
- **Hunter's claim:** Every signup writes the user's plaintext password to the application log (128-131).
- **Skeptic's response:** ACCEPT (99%). The call is unconditional and runs before the INSERT.
- **Your analysis:** The `logger.info` call at 128-131 includes `password`, and INFO is the default level (line 19). I ran it: the log held `password: hunter2`, and a duplicate signup that got 409 was logged with its password too. The Hunter is right.
- **VERDICT: REAL BUG**
- **Confidence:** High
- **True severity:** Critical
- **Suggested fix:** Remove the password (and ideally the email) from the log line; log the username only.
- **Fix complexity:** Trivial
- **Fix risks:** None in code. Existing logs already hold passwords and need purging, and affected users should change passwords.
---
**BUG-34**
- **Hunter's claim:** The server always binds every interface over plain HTTP, and its file paths depend on the working directory.
- **Skeptic's response:** ACCEPT (50%). Says most of it is deployment choice, not a defect.
- **Your analysis:** `HOST = "0.0.0.0"` (line 16) is how a network server is made reachable, TLS is commonly ended at a proxy, and `DB_PATH` can already be overridden. The crashes on a bad `PORT` or `LOG_LEVEL` are loud startup failures, which the Hunter agrees is correct. I find no wrong result: the exposure described comes from the missing auth in BUG-8, not from the bind address. These are deployment choices, so I overrule the Skeptic's hedged accept.
- **VERDICT: NOT A BUG**
- **Confidence:** Medium
---

===REFEREE_REPORT_START===

**VERIFIED BUG REPORT**

Stats:
- Total reported by Hunter: 67
- Dismissed as false positives: 14
- Confirmed as real bugs: 53
- Critical: 6 | Medium: 22 | Low: 25

### Critical

---
### BUG-1 — SQL injection in user search
- **File:** benchmarks/easy/vuln_api_server.py:97-98
- **Category:** security
- **Test coverage:** Untested
- **Fix:** Bind the value: `WHERE username LIKE ?` with `(f"%{search}%",)`.
- **Fix complexity:** Trivial
- **Fix risks:** None expected. `%` and `_` in the search text still act as LIKE wildcards, as they do today.
- **What happens:** `search` from the query string is formatted into the SQL text and executed. A `' UNION SELECT ...--` payload returns any column of any table; I got both users' password hashes back in the `email` field. A lone quote raises `sqlite3.OperationalError` and the client gets an empty reply.
- **Real-world impact:** Any unauthenticated client can read the whole database with one GET. `sqlite3` refuses stacked statements, so this path reads but does not write. Nothing mitigates it.
- **Risk if unfixed:** Full disclosure of every account and hash, which BUG-4 makes easy to crack.
---
### BUG-2 — Path traversal in file download
- **File:** benchmarks/easy/vuln_api_server.py:76-77, 171-176
- **Category:** security
- **Test coverage:** Untested
- **Fix:** Resolve the joined path with `os.path.realpath` and reject it unless it stays inside the resolved `uploads` folder.
- **Fix complexity:** Trivial
- **Fix risks:** Low. Symlinks inside `uploads` that point elsewhere stop working, which is the safe outcome. Run the check on the decoded name (see BUG-31).
- **What happens:** The path after `/api/files/` is joined onto `uploads` unchecked and opened. `/api/files/../users.db` returned the SQLite file, and `/api/files//<absolute path>` returned a file by absolute path, because `os.path.join` drops `uploads` when the second part is absolute.
- **Real-world impact:** Any unauthenticated client can read any file the server process can read: the user database, source, keys, `/etc/passwd`. The `..` form needs a raw client; the absolute form works from any client.
- **Risk if unfixed:** Read access to the host's files for anyone who can reach the port.
---
### Medium

---
### BUG-7 — Login token is the sequential user id
- **File:** benchmarks/easy/vuln_api_server.py:51-60, 156-160
- **Category:** security
- **Test coverage:** Untested
- **Fix:** Issue a random token from `secrets`, store it server-side with an expiry, and look sessions up by that token; return only non-sensitive fields from `authenticate`.
- **Fix complexity:** Moderate
- **Fix risks:** Needs a session table or store and changes what the login token looks like. Existing clients must log in again.
- **What happens:** Login returns `{"token": "<row id>"}`, and `authenticate` treats any bearer value as a row id. Login gave `"token": "1"`, and `Bearer 2` resolved to another user's full row, hash included, with no password.
- **Real-world impact:** Nothing calls `authenticate` today (BUG-8), so the bypass is latent. The moment it is wired in, anyone can act as any user by sending a small integer. Tokens never expire and cannot be revoked.
- **Risk if unfixed:** Adding auth checks on top of this token gives no protection at all.
---
### BUG-8 — No authentication on any route, including admin export
- **File:** benchmarks/easy/vuln_api_server.py:51-60, 65-79, 164-169
- **Category:** security
- **Test coverage:** Untested
- **Fix:** Require a valid session on every route except signup and login, require the admin role for `/api/admin/export`, and drop `password_hash` from the export.
- **Fix complexity:** Moderate
- **Fix risks:** Depends on a real token scheme (BUG-7). Unauthenticated clients stop working, which is intended.
- **What happens:** No handler reads the `Authorization` header. `GET /api/admin/export` with no headers returned 200 with every column for every user, including `password_hash`; `/api/users` and `/api/user?id=N` are open too.
- **Real-world impact:** Any network client can dump all accounts and hashes in one request. Rated Medium because the rubric puts missing endpoint auth there; the effect is at the top of that band.
- **Risk if unfixed:** Standing unauthenticated access to all user data.
---
### Low

---
### BUG-29 — No limit on login attempts
- **File:** benchmarks/easy/vuln_api_server.py:146-162
- **Category:** security
- **Test coverage:** Untested
- **Fix:** Track failed attempts per username and per client address and add a delay or temporary lock.
- **Fix complexity:** Moderate
- **Fix risks:** Lockout lets an attacker lock out real users; prefer delays or per-address limits. Needs a schema change or an in-memory store.
- **What happens:** `handle_login` keeps no state between attempts. Any number of guesses per user is accepted, each costing one MD5.
- **Real-world impact:** Unlimited online password guessing unless a proxy in front limits it. Low because the server has much larger holes.
- **Risk if unfixed:** Weak passwords fall to online guessing once the bigger leaks are fixed.
---
### BUG-30 — Download handler catches only `FileNotFoundError`
- **File:** benchmarks/easy/vuln_api_server.py:174-182
- **Category:** error-handling
- **Test coverage:** Untested
- **Fix:** Catch `OSError` and map it to 404 or 403; stream the file in chunks.
- **Fix complexity:** Trivial
- **Fix risks:** None expected.
- **What happens:** `GET /api/files/` raised `IsADirectoryError` and the client got an empty reply. By reading, `PermissionError` takes the same path, and the whole file is read into memory before the first byte is sent.
- **Real-world impact:** Dropped connections instead of 403 or 404, and memory spikes on large files.
- **Risk if unfixed:** Confusing client errors; a large file can exhaust memory.
---
### Low-confidence items (flagged for manual review)

- BUG-13 (confirmed, Medium): no practical attack while salts stay secret. Decide whether a hardening gap with no shown exploit should stay at Medium.
- BUG-17 (confirmed, Medium): severity. Check whether any module outside the target imports `SECRET_KEY`; if none does, treat as Low.

### Dismissed

- BUG-34: deployment choices (bind address, TLS at a proxy, relative paths). A bad `PORT` or `LOG_LEVEL` fails loudly at startup. The exposure described is BUG-8.
- BUG-35: stock `send_error` pages with correct status codes; no contract for JSON error bodies; cosmetic.

===REFEREE_REPORT_END===
