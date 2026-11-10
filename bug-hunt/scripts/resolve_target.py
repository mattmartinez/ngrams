#!/usr/bin/env python3
"""Resolve a scan target into a list of in-scope files, an optional diff, and a profile.

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
from typing import List, Optional, Sequence, Set, Tuple

SKIP_DIRS = {"node_modules", "vendor", "dist", "__pycache__", ".git"}
SKIP_NAME_PARTS = (".test.", ".spec.")
PREFIX = "[resolve_target]"


class ResolveError(Exception):
    """A user-facing error: printed as one line, exit code 2."""


def log(msg: str) -> None:
    print(f"{PREFIX} {msg}", file=sys.stderr, flush=True)


def run(cmd: Sequence[str], cwd: Optional[str] = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run a command, log it, and return the result. Raise ResolveError on failure when check is set."""
    log(f"run: {' '.join(cmd)}" + (f"  (cwd={cwd})" if cwd else ""))
    try:
        proc = subprocess.run(list(cmd), cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
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
    return proc.stdout.decode("utf-8", "replace")


def git(args: Sequence[str], cwd: str, check: bool = True) -> subprocess.CompletedProcess:
    return run(["git", "-c", "core.quotepath=false", *args], cwd=cwd, check=check)


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
            if in_scope(os.path.relpath(full, root), exts, names):
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
    root = os.path.realpath(out(proc).strip())
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


def empty_tree(repo: str) -> str:
    proc = run(["git", "hash-object", "-t", "tree", os.devnull], cwd=repo)
    return out(proc).strip()


def git_changed(repo: str, old: str, new: str) -> Tuple[List[str], bytes]:
    names = git(["diff", "--name-only", "-z", "--no-ext-diff", old, new], cwd=repo)
    files = [f for f in out(names).split("\0") if f]
    diff = git(["diff", "--no-color", "--no-ext-diff", old, new], cwd=repo)
    return files, diff.stdout


def filter_changed(repo: str, changed: List[str], exts: Set[str], names: Set[str], notes: List[str]) -> List[str]:
    kept, filtered, missing = [], 0, 0
    for rel in changed:
        if not in_scope(rel, exts, names):
            filtered += 1
            continue
        full = os.path.join(repo, rel)
        if not os.path.isfile(full):
            missing += 1
            continue
        kept.append(full)
    log(f"changed files: {len(changed)} total, {len(kept)} in scope, {filtered} skipped by filters, {missing} missing on disk")
    if filtered:
        notes.append(f"{filtered} of {len(changed)} changed files were skipped by the file-type and folder filters.")
    if missing:
        notes.append(f"Dropped {missing} of {len(changed)} changed files: not in the working tree (deleted, or not checked out).")
    return sorted(set(kept))


DIFF_CHUNK = 200


def files_match(repo: str, commit: str, paths: List[str]) -> bool:
    """True when every path is the same in `commit` and in the working tree."""
    log(f"comparing {len(paths)} in-scope paths between {commit} and the working tree")
    for i in range(0, len(paths), DIFF_CHUNK):
        chunk = paths[i:i + DIFF_CHUNK]
        proc = run(["git", "--literal-pathspecs", "diff", "--quiet", "--no-ext-diff", commit, "--", *chunk],
                   cwd=repo, check=False)
        if proc.returncode == 1:
            log(f"files differ in chunk starting at path {i}")
            return False
        if proc.returncode != 0:
            detail = proc.stderr.decode("utf-8", "replace").strip().splitlines()
            raise ResolveError(f"could not compare files with {commit[:12]}: {detail[0] if detail else 'no error output'}")
    log("in-scope files are identical to the commit under review")
    return True


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
        "file_count": 0, "files_list": None, "diff_file": None, "checked_out": None,
        "head": None, "expected_head": None, "profile": None, "notes": notes,
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
        log(f"path target: {target}, {len(files)} files in scope")
        repo = repo_root_of(start, required=False)
        result["repo_root"] = repo
        if repo:
            result["head"] = head_of(repo, required=False)
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
            mb = git(["merge-base", "HEAD", base_ref], cwd=repo, check=False)
            if mb.returncode != 0:
                raise ResolveError(f"HEAD and '{base}' have no common ancestor")
            mb_sha = out(mb).strip()
            log(f"base branch: {base}, merge-base: {mb_sha}")
            changed, diff_bytes = git_changed(repo, mb_sha, "HEAD")
            result.update(base=base, expected_head=head, checked_out=True)
        elif mode == "commit":
            sha = verify_commit(repo, value)
            if not sha:
                raise ResolveError(f"unknown commit: {value}")
            parents = out(git(["rev-list", "--parents", "-n", "1", sha], cwd=repo)).split()[1:]
            old = parents[0] if parents else empty_tree(repo)
            if not parents:
                log("root commit: comparing against the empty tree")
            if len(parents) > 1:
                notes.append("This is a merge commit; the diff is against its first parent.")
            changed, diff_bytes = git_changed(repo, old, sha)
            result.update(commit=sha, expected_head=sha, checked_out=(head == sha))
        else:  # pr
            names_proc = run(["gh", "pr", "diff", value, "--name-only"], cwd=repo)
            changed = [line.strip() for line in out(names_proc).splitlines() if line.strip()]
            diff_bytes = run(["gh", "pr", "diff", value], cwd=repo).stdout
            pr_head = out(run(["gh", "pr", "view", value, "--json", "headRefOid", "-q", ".headRefOid"], cwd=repo)).strip()
            if not pr_head:
                raise ResolveError(f"gh returned no head commit for PR {value}")
            result.update(pr=int(value), expected_head=pr_head, checked_out=(head == pr_head))
        if mode in ("commit", "pr") and result["checked_out"] is False:
            expected = result["expected_head"]
            scoped = [rel for rel in changed if in_scope(rel, ns.exts, names)]
            if mode == "pr" and not verify_commit(repo, expected):
                log(f"PR head {expected} is not a local object; skipping the content comparison")
                notes.append("The PR's head commit is not in the local repository.")
            elif files_match(repo, expected, scoped):
                result["checked_out"] = True
                notes.append("HEAD is not the commit under review, but the files under review are unchanged since it.")
        files = filter_changed(repo, changed, ns.exts, names, notes)
        log(f"checked_out: {result['checked_out']} (head {head}, expected {result['expected_head']})")
        if result["checked_out"] is False:
            notes.append(f"The code under review ({result['expected_head'][:12]}) is not what is checked out ({head[:12]}).")

    if not files:
        notes.append("No files are in scope.")

    tmp = tempfile.mkdtemp(prefix="resolve_target-")
    files_list = os.path.join(tmp, "files.txt")
    with open(files_list, "w", encoding="utf-8") as fh:
        fh.write("".join(f + "\n" for f in files))
    result["files_list"] = files_list
    result["file_count"] = len(files)
    log(f"wrote {len(files)} paths to {files_list}")
    if diff_bytes is not None:
        diff_file = os.path.join(tmp, "changes.diff")
        with open(diff_file, "wb") as fh:
            fh.write(diff_bytes)
        result["diff_file"] = diff_file
        log(f"wrote {len(diff_bytes)} bytes of diff to {diff_file}")
        if not diff_bytes.strip():
            notes.append("The diff is empty.")

    result["profile"] = find_profile(start, ns.profile_name)
    log(f"done: mode={mode} file_count={len(files)} notes={len(notes)}")
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
