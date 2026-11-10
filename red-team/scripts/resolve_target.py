#!/usr/bin/env python3
"""Resolve a scan target into a scope manifest, an optional diff, and a profile.

Usage:
    python3 resolve_target.py --profile-name NAME --ext a,b,c [--name FILE ...] -- [target args]

Target args are one of: nothing (current directory), a directory, a file,
--diff, --commit <sha>, --pr <number>.

Prints one JSON object on stdout. Logs to stderr with a [resolve_target] prefix.
Exits 2 with one error line on stderr for bad input. Never changes the working tree.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Dict, List, Optional, Sequence, Set, Tuple

SKIP_DIRS = {"node_modules", "vendor", "dist", "__pycache__", ".git"}
SKIP_NAME_PARTS = (".test.", ".spec.")
PREFIX = "[resolve_target]"


class ResolveError(Exception):
    """A user-facing error: printed as one line, exit code 2."""


def log(msg: str) -> None:
    print(f"{PREFIX} {msg}", file=sys.stderr, flush=True)


def run(cmd: Sequence[str], cwd: Optional[str] = None, check: bool = True,
        env: Optional[Dict[str, str]] = None) -> subprocess.CompletedProcess:
    """Run a command, log it, and return the result. Raise ResolveError on failure when check is set."""
    log(f"run: {' '.join(cmd)}" + (f"  (cwd={cwd})" if cwd else ""))
    try:
        proc = subprocess.run(list(cmd), cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError:
        raise ResolveError(f"'{cmd[0]}' was not found on PATH")
    except OSError as exc:
        raise ResolveError(f"could not run '{cmd[0]}': {exc}")
    log(f"exit {proc.returncode}, {len(proc.stdout)} bytes of output")
    if check and proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        first = detail[0] if detail else "no error output"
        raise ResolveError(f"'{' '.join(cmd)}' failed (exit {proc.returncode}): {first}")
    return proc


def out(proc: subprocess.CompletedProcess) -> str:
    # surrogateescape round-trips every POSIX filename through JSON's escapes.
    return proc.stdout.decode("utf-8", "surrogateescape")


def git(args: Sequence[str], cwd: str, check: bool = True,
        env: Optional[Dict[str, str]] = None) -> subprocess.CompletedProcess:
    return run(["git", "-c", "core.quotepath=false", *args], cwd=cwd, check=check, env=env)


# ---------------------------------------------------------------- arguments


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # one line, exit 2, no usage dump
        raise ResolveError(f"bad arguments: {message}")


def parse_args(argv: List[str]) -> Tuple[argparse.Namespace, List[str]]:
    if "--" in argv:
        i = argv.index("--")
        opts, raw = argv[:i], argv[i + 1:]
    else:
        opts, raw = argv, []
    parser = _Parser(prog="resolve_target.py", add_help=True)
    parser.add_argument("--profile-name", required=True, help="profile file name to look for, e.g. .bug-hunt-profile.md")
    parser.add_argument("--ext", required=True, help="comma-separated extensions in scope, e.g. ts,js,py")
    parser.add_argument("--name", action="append", default=[], help="exact file name in scope (repeatable)")
    ns = parser.parse_args(opts)
    ns.exts = {e.strip().lstrip(".") for e in ns.ext.split(",") if e.strip().lstrip(".")}
    if not ns.exts and not ns.name:
        raise ResolveError("bad arguments: --ext is empty and no --name was given")
    if not ns.profile_name.strip() or "/" in ns.profile_name:
        raise ResolveError("bad arguments: --profile-name must be a plain file name")
    return ns, raw


def parse_target(raw: List[str]) -> Tuple[str, Optional[str]]:
    """Return (mode, value). mode is path, diff, commit or pr."""
    if not raw:
        return "path", None
    head = raw[0]
    if head == "--diff":
        if len(raw) != 1:
            raise ResolveError(f"bad arguments: --diff takes no value, got: {' '.join(raw[1:])}")
        return "diff", None
    if head == "--commit":
        if len(raw) != 2 or not raw[1] or raw[1].startswith("-"):
            raise ResolveError("bad arguments: --commit needs exactly one commit id, e.g. --commit abc1234")
        return "commit", raw[1]
    if head == "--pr":
        if len(raw) != 2:
            raise ResolveError("bad arguments: --pr needs exactly one PR number, e.g. --pr 42")
        num = raw[1].lstrip("#")
        if not num.isdigit():
            raise ResolveError(f"bad arguments: --pr needs a number, got '{raw[1]}'")
        return "pr", num
    if head.startswith("-"):
        raise ResolveError(f"bad arguments: unknown option '{head}' (use a path, --diff, --commit <sha> or --pr <number>)")
    if len(raw) != 1:
        raise ResolveError(f"bad arguments: expected one path, got {len(raw)}: {' '.join(raw)}")
    return "path", head


# ---------------------------------------------------------------- filters


def in_scope(rel_path: str, exts: Set[str], names: Set[str]) -> bool:
    parts = [p for p in rel_path.replace(os.sep, "/").split("/") if p]
    if not parts:
        return False
    if any(p in SKIP_DIRS for p in parts[:-1]) or parts[-1] in SKIP_DIRS:
        return False
    name = parts[-1]
    if any(s in name for s in SKIP_NAME_PARTS):
        return False
    if name in names:
        return True
    ext = os.path.splitext(name)[1].lstrip(".")
    return bool(ext) and ext in exts


def walk_dir(root: str, exts: Set[str], names: Set[str]) -> List[str]:
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            if (
                in_scope(os.path.relpath(full, root), exts, names)
                and os.path.isfile(full)
                and os.access(full, os.R_OK)
            ):
                found.append(full)
    return sorted(found)


# ---------------------------------------------------------------- git helpers


def repo_root_of(start: str, required: bool) -> Optional[str]:
    if not shutil.which("git"):
        if required:
            raise ResolveError("'git' was not found on PATH")
        log("git not found on PATH; skipping repository detection")
        return None
    proc = git(["rev-parse", "--show-toplevel"], cwd=start, check=False)
    if proc.returncode != 0:
        if required:
            raise ResolveError(f"not a git repository: {start}")
        log("target is not inside a git repository")
        return None
    raw_root = out(proc)
    root = os.path.realpath(raw_root[:-1] if raw_root.endswith("\n") else raw_root)
    log(f"repo root: {root}")
    return root


def head_of(repo: str, required: bool) -> Optional[str]:
    proc = git(["rev-parse", "--verify", "-q", "HEAD^{commit}"], cwd=repo, check=False)
    if proc.returncode != 0:
        if required:
            raise ResolveError(f"repository has no commits yet: {repo}")
        return None
    return out(proc).strip()


def verify_commit(repo: str, ref: str) -> Optional[str]:
    proc = git(["rev-parse", "--verify", "-q", f"{ref}^{{commit}}"], cwd=repo, check=False)
    return out(proc).strip() if proc.returncode == 0 else None


def default_base(repo: str, notes: List[str]) -> Tuple[str, str]:
    """Return (base branch name, ref to use for merge-base)."""
    proc = git(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], cwd=repo, check=False)
    name = out(proc).strip() if proc.returncode == 0 else ""
    if name.startswith("origin/"):
        name = name[len("origin/"):]
    if name:
        log(f"remote default branch: {name}")
        if verify_commit(repo, f"refs/heads/{name}"):
            return name, f"refs/heads/{name}"
        if verify_commit(repo, f"refs/remotes/origin/{name}"):
            notes.append(f"No local '{name}' branch; compared against 'origin/{name}' instead.")
            return name, f"refs/remotes/origin/{name}"
        log(f"remote default '{name}' has no local or remote-tracking ref; falling back")
    for cand in ("main", "master"):
        if verify_commit(repo, f"refs/heads/{cand}"):
            log(f"base branch from fallback: {cand}")
            return cand, f"refs/heads/{cand}"
    raise ResolveError("could not find a base branch: no origin/HEAD, and no 'main' or 'master' branch")


def commit_parents(repo: str, sha: str) -> List[str]:
    """Read parents from the commit object, not from shallow-history traversal."""
    raw = out(git(["cat-file", "-p", sha], cwd=repo))
    parents = [line[len("parent "):] for line in raw.splitlines() if line.startswith("parent ")]
    if parents and not verify_commit(repo, parents[0]):
        raise ResolveError(
            f"parent commit {parents[0][:12]} of {sha[:12]} is unavailable; "
            "fetch more history (for example, git fetch --deepen=1) and rerun"
        )
    return parents


def empty_tree(repo: str) -> str:
    proc = run(["git", "hash-object", "-t", "tree", os.devnull], cwd=repo)
    return out(proc).strip()


def git_changed(repo: str, old: str, new: str) -> List[Tuple[str, str]]:
    """Return every changed path and its review status, without rename detection."""
    proc = git(["diff", "--name-status", "-z", "--no-renames", "--no-ext-diff", old, new], cwd=repo)
    fields = out(proc).split("\0")
    if fields and fields[-1] == "":
        fields.pop()
    changed = []
    i = 0
    while i < len(fields):
        code = fields[i]
        i += 1
        if "\t" in code:  # tolerate Git versions that retain the status/path tab
            code, rel = code.split("\t", 1)
        else:
            if i >= len(fields):
                raise ResolveError("could not parse git's changed-file output")
            rel = fields[i]
            i += 1
        if code == "A":
            status = "added"
        elif code == "D":
            status = "deleted"
        else:
            status = "modified"
        changed.append((rel, status))
    changed.sort(key=lambda item: item[0])
    return changed


DIFF_CHUNK = 200


def scoped_diff(repo: str, old: str, new: str, paths: List[str]) -> bytes:
    chunks = []
    for start in range(0, len(paths), DIFF_CHUNK):
        chunk = paths[start:start + DIFF_CHUNK]
        # Literal pathspecs preserve names containing glob or pathspec magic bytes.
        proc = run(
            ["git", "--literal-pathspecs", "-c", "core.quotepath=false", "diff", "--no-color",
             "--no-ext-diff", "--no-renames", old, new, "--", *chunk],
            cwd=repo,
        )
        chunks.append(proc.stdout)
    return b"".join(chunks)


def merge_base(repo: str, old: str, new: str, description: str) -> str:
    proc = git(["merge-base", old, new], cwd=repo, check=False)
    if proc.returncode != 0:
        raise ResolveError(f"{description} have no common ancestor")
    return out(proc).strip()


# ---------------------------------------------------------------- snapshots and manifest


def neutralize_escaping_symlinks(snapshot: str, notes: List[str]) -> None:
    """Keep internal links, but turn links escaping the snapshot into link-text files."""
    root = os.path.realpath(snapshot)
    replaced = 0
    for dirpath, dirnames, filenames in os.walk(snapshot, topdown=False, followlinks=False):
        for name in dirnames + filenames:
            path = os.path.join(dirpath, name)
            if not os.path.islink(path):
                continue
            resolved = os.path.realpath(path)
            try:
                inside = os.path.commonpath([root, resolved]) == root
            except ValueError:
                inside = False
            if inside:
                continue
            link_text = os.readlink(path)
            os.unlink(path)
            with open(path, "w", encoding="utf-8", errors="surrogateescape", newline="") as fh:
                fh.write(link_text)
            replaced += 1
    if replaced:
        noun = "symlink" if replaced == 1 else "symlinks"
        notes.append(
            f"Replaced {replaced} snapshot {noun} whose resolved target escaped the snapshot "
            "with regular files containing the tracked link text."
        )
    log(f"snapshot symlink safety: {replaced} escaping links replaced")


def make_snapshot(repo: str, commit: str, tmp: str, notes: List[str]) -> str:
    """Materialize commit through a temporary index, never the repository index or worktree."""
    snapshot = os.path.join(tmp, "snapshot")
    os.makedirs(snapshot)
    index = os.path.join(tmp, "index")
    env = os.environ.copy()
    env["GIT_INDEX_FILE"] = index
    git(["read-tree", commit], cwd=repo, env=env)
    git(["checkout-index", "--all", f"--prefix={snapshot}{os.sep}"], cwd=repo, env=env)
    neutralize_escaping_symlinks(snapshot, notes)
    log(f"immutable snapshot: {snapshot} (commit {commit[:12]})")
    return snapshot


def scope_entries(snapshot: str, changed: List[Tuple[str, str]], exts: Set[str], names: Set[str],
                  notes: List[str]) -> Tuple[List[dict], int]:
    entries = []
    filtered = 0
    for rel, status in changed:
        scoped = in_scope(rel, exts, names)
        if not scoped:
            filtered += 1
        candidate = os.path.join(snapshot, *rel.split("/"))
        review_path = None
        if status != "deleted" and os.path.isfile(candidate):
            review_path = candidate
        reason = None
        if not scoped:
            reason = "Excluded by file-type, test, or folder filters."
        elif status != "deleted" and review_path is None:
            reason = "Not a readable regular file in the immutable snapshot."
        entries.append({
            "path": rel,
            "old_path": None,
            "status": status,
            "in_scope": scoped,
            "reason": reason,
            "review_path": review_path,
        })
    if filtered:
        notes.append(f"{filtered} of {len(changed)} changed files were skipped by the file-type and folder filters.")
    change_count = sum(1 for rel, _status in changed if in_scope(rel, exts, names))
    return entries, change_count


def write_scope(tmp: str, root: str, entries: List[dict]) -> str:
    scope_file = os.path.join(tmp, "scope.json")
    with open(scope_file, "w", encoding="utf-8") as fh:
        json.dump({"version": 1, "root": root, "entries": entries}, fh, indent=2)
        fh.write("\n")
    log(f"wrote {len(entries)} manifest entries to {scope_file}")
    return scope_file


# ---------------------------------------------------------------- profile


def find_profile(start: str, profile_name: str) -> Optional[str]:
    d = os.path.realpath(start)
    log(f"profile search for '{profile_name}' starting at {d}")
    while True:
        cand = os.path.join(d, profile_name)
        if os.path.isfile(cand):
            log(f"profile found: {cand}")
            return cand
        parent = os.path.dirname(d)
        if parent == d:
            log("no profile found")
            return None
        d = parent


# ---------------------------------------------------------------- main


def resolve(argv: List[str]) -> dict:
    ns, raw = parse_args(argv)
    names = set(ns.name)
    mode, value = parse_target(raw)
    log(f"mode: {mode}" + (f" ({value})" if value else ""))
    log(f"extensions: {','.join(sorted(ns.exts)) or '-'}; names: {','.join(sorted(names)) or '-'}")
    cwd = os.path.realpath(os.getcwd())
    notes: List[str] = []
    result = {
        "mode": mode, "target": None, "repo_root": None, "base": None, "commit": None, "pr": None,
        "file_count": 0, "change_count": None, "diff_file": None, "checked_out": None,
        "head": None, "expected_head": None, "profile": None, "notes": notes,
        "assessment_root": None, "snapshot_root": None, "scope_file": None,
    }
    diff_bytes: Optional[bytes] = None

    if mode == "path":
        target = os.path.realpath(os.path.join(cwd, value) if value else cwd)
        if not os.path.exists(target):
            raise ResolveError(f"path does not exist: {value}")
        result["target"] = target
        if os.path.isdir(target):
            files = walk_dir(target, ns.exts, names)
            start = target
        else:
            files = [target]
            start = os.path.dirname(target)
        assessment_root = start
        log(f"path target: {target}, {len(files)} files in scope")
        repo = repo_root_of(start, required=False)
        result["repo_root"] = repo
        if repo:
            result["head"] = head_of(repo, required=False)
        entries = [{
            "path": os.path.relpath(full, assessment_root).replace(os.sep, "/"),
            "old_path": None,
            "status": "present",
            "in_scope": True,
            "reason": None,
            "review_path": full,
        } for full in files]
        result["file_count"] = len(files)
        result["assessment_root"] = assessment_root
        tmp = tempfile.mkdtemp(prefix="resolve_target-")
    else:
        if mode == "pr" and not shutil.which("gh"):
            raise ResolveError("the GitHub CLI 'gh' was not found on PATH; it is needed for --pr")
        repo = repo_root_of(cwd, required=True)
        result["repo_root"] = repo
        start = repo
        head = head_of(repo, required=True)
        result["head"] = head
        if mode == "diff":
            base, base_ref = default_base(repo, notes)
            mb = merge_base(repo, "HEAD", base_ref, f"HEAD and '{base}'")
            log(f"base branch: {base}, merge-base: {mb}")
            old, new = mb, head
            result.update(base=base, expected_head=head, checked_out=True)
        elif mode == "commit":
            sha = verify_commit(repo, value)
            if not sha:
                raise ResolveError(f"unknown commit: {value}")
            parents = commit_parents(repo, sha)
            old = parents[0] if parents else empty_tree(repo)
            if not parents:
                log("root commit: comparing against the empty tree")
            if len(parents) > 1:
                notes.append("This is a merge commit; the diff is against its first parent.")
            new = sha
            result.update(commit=sha, expected_head=sha, checked_out=(head == sha))
        else:  # pr
            proc = run(["gh", "pr", "view", value, "--json", "headRefOid,baseRefOid"], cwd=repo)
            try:
                payload = json.loads(out(proc))
            except (ValueError, TypeError) as exc:
                raise ResolveError(f"gh returned invalid JSON for PR {value}: {exc}")
            if not isinstance(payload, dict):
                raise ResolveError(f"gh returned no object for PR {value}")
            head_oid = payload.get("headRefOid")
            base_oid = payload.get("baseRefOid")
            if not isinstance(head_oid, str) or not head_oid or not isinstance(base_oid, str) or not base_oid:
                raise ResolveError(f"gh returned no head or base commit for PR {value}")
            new = verify_commit(repo, head_oid)
            base_sha = verify_commit(repo, base_oid)
            if not new or not base_sha:
                raise ResolveError(
                    f"PR {value} base and head commits are not both available locally; fetch the base branch "
                    f"and PR head, then check out the PR with 'gh pr checkout {value}' or rerun after fetching"
                )
            old = merge_base(repo, base_sha, new, f"PR {value} base and head")
            result.update(
                base=base_sha, commit=new, pr=int(value), expected_head=new,
                checked_out=(head == new),
            )

        changed = git_changed(repo, old, new)
        scoped_paths = [rel for rel, _status in changed if in_scope(rel, ns.exts, names)]
        diff_bytes = scoped_diff(repo, old, new, scoped_paths)
        tmp = tempfile.mkdtemp(prefix="resolve_target-")
        snapshot = make_snapshot(repo, new, tmp, notes)
        entries, change_count = scope_entries(snapshot, changed, ns.exts, names, notes)
        result["file_count"] = sum(
            1 for entry in entries if entry["in_scope"] and entry["review_path"] is not None
        )
        result["change_count"] = change_count
        result["assessment_root"] = snapshot
        result["snapshot_root"] = snapshot
        assessment_root = snapshot
        log(f"checked_out: {result['checked_out']} (head {head}, expected {result['expected_head']})")
        if result["checked_out"] is False:
            notes.append(
                f"HEAD ({head[:12]}) differs from the commit under review ({result['expected_head'][:12]}); "
                "review files come from the immutable snapshot."
            )

    if (mode == "path" and result["file_count"] == 0) or (mode != "path" and result["change_count"] == 0):
        notes.append("No files are in scope.")

    result["scope_file"] = write_scope(tmp, assessment_root, entries)
    if diff_bytes is not None:
        diff_file = os.path.join(tmp, "changes.diff")
        with open(diff_file, "wb") as fh:
            fh.write(diff_bytes)
        result["diff_file"] = diff_file
        log(f"wrote {len(diff_bytes)} bytes of diff to {diff_file}")
        if not diff_bytes.strip():
            notes.append("The diff is empty.")

    result["profile"] = find_profile(start, ns.profile_name)
    log(f"done: mode={mode} file_count={result['file_count']} change_count={result['change_count']} notes={len(notes)}")
    return result


def main(argv: Optional[List[str]] = None) -> int:
    try:
        result = resolve(sys.argv[1:] if argv is None else argv)
    except ResolveError as exc:
        print(f"{PREFIX} error: {exc}", file=sys.stderr, flush=True)
        return 2
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
