#!/usr/bin/env python3
"""Tests for resolve_target.py. Run: python3 -I test_resolve_target.py

Each test builds a throwaway git repository in a temp directory. No network,
no user git config, no real gh.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

SCRIPT = os.path.join(os.path.dirname(os.path.realpath(__file__)), "resolve_target.py")
PROFILE = ".bug-hunt-profile.md"
EXTS = "ts,js,py,go,rs,rb,java,swift,kt,c,cpp,h"


def clean_env(tmp):
    """Environment that ignores the user's git config and pins an identity."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        HOME=tmp,
        XDG_CONFIG_HOME=os.path.join(tmp, ".config"),
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_SYSTEM=os.devnull,
        GIT_AUTHOR_NAME="Test",
        GIT_AUTHOR_EMAIL="test@example.invalid",
        GIT_COMMITTER_NAME="Test",
        GIT_COMMITTER_EMAIL="test@example.invalid",
        GIT_CEILING_DIRECTORIES=tmp,
    )
    return env


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="rt-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.env = clean_env(self.tmp)
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)

    # helpers -------------------------------------------------------------
    def git(self, *args, cwd=None):
        proc = subprocess.run(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", *args],
            cwd=cwd or self.repo, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        return proc.stdout.decode().strip()

    def init(self, branch="main"):
        self.git("init", "-q")
        self.git("symbolic-ref", "HEAD", f"refs/heads/{branch}")

    def write(self, rel, text="x = 1\n", root=None):
        path = os.path.join(root or self.repo, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(text)
        return path

    def commit(self, msg="c"):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg)
        return self.git("rev-parse", "HEAD")

    def run_script(self, *target, cwd=None, env=None, extra=()):
        cmd = [sys.executable, "-I", SCRIPT, "--profile-name", PROFILE, "--ext", EXTS, *extra, "--", *target]
        return subprocess.run(cmd, cwd=cwd or self.repo, env=env or self.env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def ok(self, *target, **kw):
        proc = self.run_script(*target, **kw)
        err = proc.stderr.decode()
        self.assertEqual(proc.returncode, 0, err)
        self.assertNotIn("Traceback", err)
        self.assertIn("[resolve_target]", err)
        data = json.loads(proc.stdout.decode())
        with open(data["files_list"]) as fh:
            data["_files"] = [line for line in fh.read().splitlines() if line]
        self.assertEqual(len(data["_files"]), data["file_count"])
        return data

    def fails(self, *target, **kw):
        proc = self.run_script(*target, **kw)
        err = proc.stderr.decode()
        self.assertEqual(proc.returncode, 2, err)
        self.assertNotIn("Traceback", err)
        self.assertEqual(proc.stdout.decode(), "")
        error_lines = [l for l in err.splitlines() if "error:" in l]
        self.assertEqual(len(error_lines), 1, err)
        return error_lines[0]

    def rel(self, data):
        return sorted(os.path.relpath(f, self.repo) for f in data["_files"])

    def read_diff(self, data):
        with open(data["diff_file"]) as fh:
            return fh.read()


class PathMode(Base):
    def make_tree(self):
        self.write("src/app.py")
        self.write("src/util.ts")
        self.write("src/lib.h")
        self.write("src/README.md")
        self.write("src/app.test.py")
        self.write("src/thing.spec.ts")
        self.write("node_modules/pkg/index.js")
        self.write("vendor/lib.go")
        self.write("dist/out.js")
        self.write("src/__pycache__/app.py")
        self.write("deep/a/b/c.rs")
        self.write("src/Dockerfile", "FROM scratch\n")

    def test_directory_with_ext_and_skip_rules(self):
        self.make_tree()
        data = self.ok(self.repo, cwd=self.tmp)
        self.assertEqual(data["mode"], "path")
        self.assertEqual(data["target"], self.repo)
        self.assertEqual(self.rel(data), ["deep/a/b/c.rs", "src/app.py", "src/lib.h", "src/util.ts"])
        self.assertIsNone(data["diff_file"])
        self.assertIsNone(data["checked_out"])
        self.assertIsNone(data["repo_root"])  # not a git repo
        self.assertIsNone(data["profile"])

    def test_name_option_adds_exact_names(self):
        self.make_tree()
        data = self.ok(self.repo, cwd=self.tmp, extra=("--name", "Dockerfile"))
        self.assertIn("src/Dockerfile", self.rel(data))

    def test_skip_rules_apply_below_target_only(self):
        self.write("vendor/proj/a.py")
        data = self.ok(os.path.join(self.repo, "vendor", "proj"), cwd=self.tmp)
        self.assertEqual(data["file_count"], 1)

    def test_single_file_target(self):
        self.make_tree()
        target = os.path.join(self.repo, "src", "README.md")
        data = self.ok("src/README.md")
        self.assertEqual(data["target"], target)
        self.assertEqual(data["_files"], [target])

    def test_no_argument_scans_cwd(self):
        self.make_tree()
        data = self.ok(cwd=os.path.join(self.repo, "src"))
        self.assertEqual(data["target"], os.path.join(self.repo, "src"))
        self.assertEqual(self.rel(data), ["src/app.py", "src/lib.h", "src/util.ts"])

    def test_profile_two_levels_above(self):
        self.write(PROFILE, "# profile\n")
        self.write("a/b/x.py")
        data = self.ok("a/b")
        self.assertEqual(data["profile"], os.path.join(self.repo, PROFILE))

    def test_profile_from_file_parent(self):
        self.write("a/" + PROFILE, "# profile\n")
        self.write("a/x.py")
        data = self.ok("a/x.py")
        self.assertEqual(data["profile"], os.path.join(self.repo, "a", PROFILE))

    def test_repo_root_and_head_in_path_mode(self):
        self.init()
        self.write("x.py")
        sha = self.commit()
        data = self.ok()
        self.assertEqual(data["repo_root"], self.repo)
        self.assertEqual(data["head"], sha)


class DiffMode(Base):
    def setup_branches(self, base):
        self.init(base)
        self.write("keep.py", "keep = 1\n")
        self.write("old.py", "old = 1\n")
        self.commit("base")
        self.git("checkout", "-q", "-b", "feature")
        self.write("new.py", "def f():\n    return 42\n")
        self.write("notes.md", "words\n")
        self.write("node_modules/x.js", "module.exports = 1\n")
        os.remove(os.path.join(self.repo, "old.py"))
        return self.commit("feature")

    def test_diff_with_main(self):
        sha = self.setup_branches("main")
        data = self.ok("--diff")
        self.assertEqual(data["mode"], "diff")
        self.assertEqual(data["base"], "main")
        self.assertEqual(self.rel(data), ["new.py"])
        self.assertIn("return 42", self.read_diff(data))
        self.assertTrue(data["checked_out"])
        self.assertEqual(data["head"], sha)
        self.assertEqual(data["expected_head"], sha)
        self.assertTrue(any("skipped by the file-type" in n for n in data["notes"]), data["notes"])
        self.assertTrue(any("not in the working tree" in n for n in data["notes"]), data["notes"])

    def test_diff_with_only_master(self):
        self.setup_branches("master")
        data = self.ok("--diff")
        self.assertEqual(data["base"], "master")
        self.assertEqual(self.rel(data), ["new.py"])

    def test_diff_uses_origin_head(self):
        self.setup_branches("trunk")
        self.git("update-ref", "refs/remotes/origin/trunk", "trunk")
        self.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
        data = self.ok("--diff")
        self.assertEqual(data["base"], "trunk")
        self.assertEqual(self.rel(data), ["new.py"])

    def test_diff_profile_starts_at_repo_root(self):
        self.setup_branches("main")
        self.write(PROFILE, "# root profile\n")
        self.write("sub/" + PROFILE, "# sub profile\n")
        data = self.ok("--diff", cwd=os.path.join(self.repo, "sub"))
        self.assertEqual(data["repo_root"], self.repo)
        self.assertEqual(data["profile"], os.path.join(self.repo, PROFILE))

    def test_diff_no_changes(self):
        self.init("main")
        self.write("a.py")
        self.commit()
        data = self.ok("--diff")
        self.assertEqual(data["file_count"], 0)
        self.assertIn("No files are in scope.", data["notes"])


class CommitMode(Base):
    def test_commit_list_diff_and_checked_out(self):
        self.init()
        self.write("a.py")
        first = self.commit("first")
        self.write("b.py", "print('hello from b')\n")
        self.write("a.py", "x = 2\n")
        second = self.commit("second")
        self.write("c.py")
        self.commit("third")

        self.git("checkout", "-q", second)
        data = self.ok("--commit", second[:8])
        self.assertEqual(data["mode"], "commit")
        self.assertEqual(data["commit"], second)
        self.assertEqual(self.rel(data), ["a.py", "b.py"])
        diff = self.read_diff(data)
        self.assertIn("hello from b", diff)
        self.assertIn("+x = 2", diff)
        self.assertTrue(data["checked_out"])

        self.assertNotEqual(first, second)

    def later_commit_setup(self):
        self.init()
        self.write("a.py", "a = 1\n")
        self.write("b.py", "b = 1\n")
        self.commit("first")
        self.write("a.py", "a = 2\n")
        self.write("b.py", "b = 2\n")
        return self.commit("under review")

    def test_commit_head_ahead_unrelated_is_checked_out(self):
        sha = self.later_commit_setup()
        self.write("c.py", "c = 1\n")
        head = self.commit("unrelated")
        data = self.ok("--commit", sha)
        self.assertEqual(data["head"], head)
        self.assertEqual(data["expected_head"], sha)
        self.assertTrue(data["checked_out"])
        self.assertIn("HEAD is not the commit under review, but the files under review are unchanged since it.",
                      data["notes"])

    def test_commit_later_change_is_not_checked_out(self):
        sha = self.later_commit_setup()
        self.write("b.py", "b = 3\n")
        self.commit("changes b")
        data = self.ok("--commit", sha)
        self.assertFalse(data["checked_out"])
        self.assertNotEqual(data["head"], sha)
        self.assertTrue(any("not what is checked out" in n for n in data["notes"]), data["notes"])

    def test_commit_later_delete_is_not_checked_out(self):
        sha = self.later_commit_setup()
        os.remove(os.path.join(self.repo, "b.py"))
        self.commit("deletes b")
        data = self.ok("--commit", sha)
        self.assertFalse(data["checked_out"])
        self.assertEqual(self.rel(data), ["a.py"])

    def test_commit_uncommitted_edit_is_not_checked_out(self):
        sha = self.later_commit_setup()
        self.write("c.py", "c = 1\n")
        self.commit("unrelated")
        self.write("a.py", "a = 99\n")  # edited, not committed
        data = self.ok("--commit", sha)
        self.assertFalse(data["checked_out"])

    def test_commit_compare_chunks_many_paths(self):
        self.init()
        self.write("seed.py", "seed\n")
        self.commit("seed")
        for i in range(450):
            self.write(f"m/f{i:03d}.py", f"v = {i}\n")
        sha = self.commit("many files")
        self.write("other.md", "o\n")
        self.commit("unrelated")
        data = self.ok("--commit", sha)
        self.assertEqual(data["file_count"], 450)
        self.assertTrue(data["checked_out"])
        self.write("m/f449.py", "changed\n")
        data = self.ok("--commit", sha)
        self.assertFalse(data["checked_out"])

    def test_root_commit(self):
        self.init()
        self.write("a.py", "root = True\n")
        root = self.commit("root")
        data = self.ok("--commit", root)
        self.assertEqual(self.rel(data), ["a.py"])
        self.assertIn("+root = True", self.read_diff(data))
        self.assertTrue(data["checked_out"])

    def test_deleted_file_dropped(self):
        self.init()
        self.write("a.py")
        self.write("b.py")
        self.commit("first")
        os.remove(os.path.join(self.repo, "b.py"))
        self.write("a.py", "x = 3\n")
        sha = self.commit("delete b")
        data = self.ok("--commit", sha)
        self.assertEqual(self.rel(data), ["a.py"])
        self.assertIn("b.py", self.read_diff(data))
        self.assertTrue(any("not in the working tree" in n for n in data["notes"]), data["notes"])


FAKE_GH = textwrap.dedent("""\
    #!{python}
    import os, sys
    here = os.path.dirname(os.path.realpath(__file__))
    args = sys.argv[1:]
    def emit(name):
        with open(os.path.join(here, name)) as fh:
            sys.stdout.write(fh.read())
    if os.path.exists(os.path.join(here, "fail")):
        sys.stderr.write("GraphQL: Could not resolve to a PullRequest\\n")
        sys.exit(1)
    if args[:2] == ["pr", "diff"] and "--name-only" in args:
        emit("names.txt")
    elif args[:2] == ["pr", "diff"]:
        emit("diff.txt")
    elif args[:2] == ["pr", "view"] and "headRefOid" in args:
        emit("head.txt")
    else:
        sys.stderr.write("fake gh: unexpected args %r\\n" % (args,))
        sys.exit(1)
""")


class PrMode(Base):
    def fake_gh(self, names, diff, head, fail=False):
        bindir = os.path.join(self.tmp, "fakebin")
        os.makedirs(bindir)
        gh = os.path.join(bindir, "gh")
        with open(gh, "w") as fh:
            fh.write(FAKE_GH.format(python=sys.executable))
        os.chmod(gh, 0o755)
        for fname, text in (("names.txt", names), ("diff.txt", diff), ("head.txt", head + "\n")):
            with open(os.path.join(bindir, fname), "w") as fh:
                fh.write(text)
        if fail:
            open(os.path.join(bindir, "fail"), "w").close()
        env = dict(self.env)
        env["PATH"] = bindir + os.pathsep + env.get("PATH", "")
        return env

    def test_pr_list_diff_and_not_checked_out(self):
        self.init()
        self.write("a.py")
        self.write("docs.md", "d\n")
        self.commit()
        env = self.fake_gh("a.py\ndocs.md\ngone.py\n", "diff --git a/a.py b/a.py\n+pr change\n", "f" * 40)
        data = self.ok("--pr", "7", env=env)
        self.assertEqual(data["mode"], "pr")
        self.assertEqual(data["pr"], 7)
        self.assertEqual(self.rel(data), ["a.py"])
        self.assertIn("+pr change", self.read_diff(data))
        self.assertEqual(data["expected_head"], "f" * 40)
        self.assertFalse(data["checked_out"])
        self.assertIn("The PR's head commit is not in the local repository.", data["notes"])

    def test_pr_head_local_files_identical(self):
        self.init()
        self.write("a.py", "a = 1\n")
        pr_head = self.commit("pr head")
        self.write("c.py", "c = 1\n")
        head = self.commit("unrelated")
        env = self.fake_gh("a.py\n", "diff --git a/a.py b/a.py\n", pr_head)
        data = self.ok("--pr", "10", env=env)
        self.assertEqual(data["head"], head)
        self.assertEqual(data["expected_head"], pr_head)
        self.assertTrue(data["checked_out"])
        self.assertIn("HEAD is not the commit under review, but the files under review are unchanged since it.",
                      data["notes"])

    def test_pr_head_local_files_differ(self):
        self.init()
        self.write("a.py", "a = 1\n")
        pr_head = self.commit("pr head")
        self.write("a.py", "a = 2\n")
        self.commit("changes a")
        env = self.fake_gh("a.py\n", "diff --git a/a.py b/a.py\n", pr_head)
        data = self.ok("--pr", "11", env=env)
        self.assertFalse(data["checked_out"])

    def test_pr_checked_out(self):
        self.init()
        self.write("a.py")
        sha = self.commit()
        env = self.fake_gh("a.py\n", "diff\n", sha)
        data = self.ok("--pr", "#8", env=env)
        self.assertTrue(data["checked_out"])

    def test_pr_gh_fails(self):
        self.init()
        self.write("a.py")
        self.commit()
        env = self.fake_gh("", "", "", fail=True)
        line = self.fails("--pr", "9", env=env)
        self.assertIn("Could not resolve", line)


class Errors(Base):
    def test_missing_path(self):
        self.assertIn("does not exist", self.fails("/no/such/path"))

    def test_diff_outside_repo(self):
        self.assertIn("not a git repository", self.fails("--diff"))

    def test_commit_outside_repo(self):
        self.assertIn("not a git repository", self.fails("--commit", "abc"))

    def test_unknown_commit(self):
        self.init()
        self.write("a.py")
        self.commit()
        self.assertIn("unknown commit", self.fails("--commit", "deadbeef"))

    def test_no_base_branch(self):
        self.init("trunk")
        self.write("a.py")
        self.commit()
        self.assertIn("base branch", self.fails("--diff"))

    def test_gh_missing(self):
        self.init()
        self.write("a.py")
        self.commit()
        env = dict(self.env)
        empty = os.path.join(self.tmp, "emptybin")
        os.makedirs(empty)
        env["PATH"] = empty
        self.assertIn("gh", self.fails("--pr", "1", env=env))

    def test_bad_arguments(self):
        self.fails("--commit")
        self.fails("--pr", "abc")
        self.fails("--diff", "extra")
        self.fails("--bogus")
        self.fails("a", "b")

    def test_missing_required_option(self):
        proc = subprocess.run([sys.executable, "-I", SCRIPT, "--ext", "py", "--"], cwd=self.repo,
                              env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(proc.returncode, 2)
        self.assertNotIn("Traceback", proc.stderr.decode())
        self.assertIn("--profile-name", proc.stderr.decode())


if __name__ == "__main__":
    unittest.main(verbosity=2)
