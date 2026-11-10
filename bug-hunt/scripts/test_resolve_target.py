#!/usr/bin/env python3
"""Tests for resolve_target.py. Run: python3 -I test_resolve_target.py

Each test builds a throwaway git repository in a temp directory. No network,
no user git config, no real gh. Tests exercise only the public CLI and its JSON
output; they never import resolver internals.
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
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def read(self, path):
        with open(path, encoding="utf-8") as fh:
            return fh.read()

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
        data = json.loads(proc.stdout.decode("utf-8"))
        self.assertNotIn("files_list", data)

        with open(data["scope_file"], encoding="utf-8") as fh:
            manifest = json.load(fh)
        self.assertEqual(manifest["version"], 1)
        self.assertEqual(manifest["root"], data["assessment_root"])
        self.assertTrue(os.path.isabs(data["assessment_root"]))
        self.assertTrue(os.path.isdir(data["assessment_root"]))
        self.assertIsInstance(manifest["entries"], list)
        data["_manifest"] = manifest
        data["_entries"] = manifest["entries"]

        for entry in manifest["entries"]:
            self.assertEqual(
                {"path", "old_path", "status", "in_scope", "reason", "review_path"} - set(entry),
                set(),
            )
            self.assertIsInstance(entry["path"], str)
            self.assertIn(entry["status"], {"present", "added", "modified", "deleted"})
            self.assertIsInstance(entry["in_scope"], bool)
            review_path = entry["review_path"]
            if entry["status"] == "deleted":
                self.assertIsNone(review_path)
            if entry["in_scope"] and entry["status"] != "deleted":
                self.assertIsNotNone(review_path, entry)
            if review_path is not None:
                self.assertTrue(os.path.isabs(review_path), entry)
                self.assertTrue(os.path.isfile(review_path), entry)
                self.assertEqual(
                    os.path.commonpath([data["assessment_root"], review_path]),
                    data["assessment_root"],
                    entry,
                )

        data["_files"] = [
            entry["review_path"] for entry in manifest["entries"]
            if entry["in_scope"] and entry["review_path"] is not None
        ]
        self.assertEqual(len(data["_files"]), data["file_count"])
        if data["mode"] == "path":
            self.assertIsNone(data["snapshot_root"])
            self.assertIsNone(data["change_count"])
        else:
            self.assertEqual(data["snapshot_root"], data["assessment_root"])
            self.assertTrue(os.path.isdir(data["snapshot_root"]))
            self.assertEqual(
                data["change_count"],
                sum(1 for entry in manifest["entries"] if entry["in_scope"]),
            )
        return data

    def fails(self, *target, **kw):
        proc = self.run_script(*target, **kw)
        err = proc.stderr.decode()
        self.assertEqual(proc.returncode, 2, err)
        self.assertNotIn("Traceback", err)
        self.assertEqual(proc.stdout.decode(), "")
        error_lines = [line for line in err.splitlines() if "error:" in line]
        self.assertEqual(len(error_lines), 1, err)
        return error_lines[0]

    def entry(self, data, path):
        matches = [entry for entry in data["_entries"] if entry["path"] == path]
        self.assertEqual(len(matches), 1, (path, data["_entries"]))
        return matches[0]

    def paths(self, data):
        return sorted(entry["path"] for entry in data["_entries"])

    def rel(self, data):
        return sorted(
            entry["path"] for entry in data["_entries"]
            if entry["in_scope"] and entry["review_path"] is not None
        )

    def read_review(self, data, path):
        review_path = self.entry(data, path)["review_path"]
        self.assertIsNotNone(review_path, path)
        return self.read(review_path)

    def read_diff(self, data):
        return self.read(data["diff_file"])


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
        self.assertEqual(data["assessment_root"], self.repo)
        self.assertEqual(self.rel(data), ["deep/a/b/c.rs", "src/app.py", "src/lib.h", "src/util.ts"])
        self.assertIsNone(data["diff_file"])
        self.assertIsNone(data["checked_out"])
        self.assertIsNone(data["repo_root"])  # not a git repo
        self.assertIsNone(data["profile"])
        self.assertTrue(all(entry["status"] == "present" for entry in data["_entries"]))

    def test_name_option_adds_exact_names(self):
        self.make_tree()
        data = self.ok(self.repo, cwd=self.tmp, extra=("--name", "Dockerfile"))
        self.assertIn("src/Dockerfile", self.rel(data))

    def test_skip_rules_apply_below_target_only(self):
        self.write("vendor/proj/a.py")
        data = self.ok(os.path.join(self.repo, "vendor", "proj"), cwd=self.tmp)
        self.assertEqual(data["file_count"], 1)
        self.assertEqual(self.rel(data), ["a.py"])

    def test_single_file_target(self):
        self.make_tree()
        target = os.path.join(self.repo, "src", "README.md")
        data = self.ok("src/README.md")
        self.assertEqual(data["target"], target)
        self.assertEqual(data["assessment_root"], os.path.dirname(target))
        self.assertEqual(data["_files"], [target])

    def test_no_argument_scans_cwd(self):
        self.make_tree()
        data = self.ok(cwd=os.path.join(self.repo, "src"))
        self.assertEqual(data["target"], os.path.join(self.repo, "src"))
        self.assertEqual(data["assessment_root"], os.path.join(self.repo, "src"))
        self.assertEqual(self.rel(data), ["app.py", "lib.h", "util.ts"])

    def test_directory_skips_dangling_symlinks(self):
        os.symlink(
            os.path.join(self.repo, "missing.py"),
            os.path.join(self.repo, "app.py"),
        )
        data = self.ok(self.repo)
        self.assertEqual(data["file_count"], 0)
        self.assertEqual(data["_entries"], [])

    def test_empty_directory_has_no_path_assessment(self):
        data = self.ok()
        self.assertEqual(data["file_count"], 0)
        self.assertIsNone(data["change_count"])
        self.assertEqual(data["_entries"], [])

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

    def test_json_manifest_preserves_newline_and_unicode_paths(self):
        newline = "src/line\nbreak.py"
        unicode_path = "src/caf\u00e9-\u96ea.py"
        self.write(newline, "newline_name = True\n")
        self.write(unicode_path, "unicode_name = True\n")
        data = self.ok()
        self.assertEqual(set(self.rel(data)), {newline, unicode_path})
        self.assertEqual(self.read_review(data, newline), "newline_name = True\n")
        self.assertEqual(self.read_review(data, unicode_path), "unicode_name = True\n")
        self.assertEqual(data["file_count"], 2)


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

    def test_diff_manifest_scoped_diff_and_deletion(self):
        sha = self.setup_branches("main")
        data = self.ok("--diff")
        self.assertEqual(data["mode"], "diff")
        self.assertEqual(data["base"], "main")
        self.assertEqual(self.rel(data), ["new.py"])
        self.assertEqual(data["file_count"], 1)
        self.assertEqual(data["change_count"], 2)
        self.assertEqual(self.paths(data), ["new.py", "node_modules/x.js", "notes.md", "old.py"])
        self.assertEqual(self.entry(data, "new.py")["status"], "added")
        self.assertTrue(self.entry(data, "new.py")["in_scope"])
        self.assertEqual(self.entry(data, "old.py")["status"], "deleted")
        self.assertTrue(self.entry(data, "old.py")["in_scope"])
        self.assertIsNone(self.entry(data, "old.py")["review_path"])
        self.assertFalse(self.entry(data, "notes.md")["in_scope"])
        self.assertFalse(self.entry(data, "node_modules/x.js")["in_scope"])
        diff = self.read_diff(data)
        self.assertIn("return 42", diff)
        self.assertIn("-old = 1", diff)
        self.assertNotIn("words", diff)
        self.assertNotIn("module.exports", diff)
        self.assertEqual(self.read_review(data, "new.py"), "def f():\n    return 42\n")
        self.assertEqual(self.read(os.path.join(data["snapshot_root"], "keep.py")), "keep = 1\n")
        self.assertFalse(os.path.exists(os.path.join(data["snapshot_root"], "old.py")))
        self.assertTrue(data["checked_out"])
        self.assertEqual(data["head"], sha)
        self.assertEqual(data["expected_head"], sha)
        self.assertTrue(any("skipped by the file-type" in note for note in data["notes"]), data["notes"])

    def test_diff_uses_immutable_head_snapshot_with_dirty_worktree(self):
        self.setup_branches("main")
        self.write("new.py", "dirty_before = True\n")
        self.write("keep.py", "dirty_helper_before = True\n")
        data = self.ok("--diff")
        self.assertEqual(self.read_review(data, "new.py"), "def f():\n    return 42\n")
        self.assertEqual(self.read(os.path.join(data["snapshot_root"], "keep.py")), "keep = 1\n")
        self.assertEqual(self.read(os.path.join(self.repo, "new.py")), "dirty_before = True\n")
        self.write("new.py", "dirty_after = True\n")
        self.write("keep.py", "dirty_helper_after = True\n")
        self.assertEqual(self.read_review(data, "new.py"), "def f():\n    return 42\n")
        self.assertEqual(self.read(os.path.join(data["snapshot_root"], "keep.py")), "keep = 1\n")
        self.assertEqual(self.read(os.path.join(self.repo, "new.py")), "dirty_after = True\n")

    def test_deletion_only_diff_is_not_an_empty_assessment(self):
        self.init("main")
        self.write("gone.py", "secret = 'old value'\n")
        self.commit("base")
        self.git("checkout", "-q", "-b", "feature")
        os.remove(os.path.join(self.repo, "gone.py"))
        self.commit("delete only")
        data = self.ok("--diff")
        self.assertEqual(data["file_count"], 0)
        self.assertEqual(data["change_count"], 1)
        self.assertEqual(self.rel(data), [])
        entry = self.entry(data, "gone.py")
        self.assertEqual(entry["status"], "deleted")
        self.assertTrue(entry["in_scope"])
        self.assertIsNone(entry["review_path"])
        self.assertIn("-secret = 'old value'", self.read_diff(data))
        self.assertFalse(os.path.exists(os.path.join(data["snapshot_root"], "gone.py")))

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
        self.assertEqual(data["change_count"], 0)
        self.assertEqual(data["_entries"], [])
        self.assertEqual(self.read_diff(data), "")
        self.assertTrue(os.path.isfile(os.path.join(data["snapshot_root"], "a.py")))


class CommitMode(Base):
    def test_commit_manifest_diff_and_snapshot(self):
        self.init()
        self.write("a.py", "x = 1\n")
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
        self.assertEqual(data["change_count"], 2)
        diff = self.read_diff(data)
        self.assertIn("hello from b", diff)
        self.assertIn("+x = 2", diff)
        self.assertEqual(self.read_review(data, "a.py"), "x = 2\n")
        self.assertFalse(os.path.exists(os.path.join(data["snapshot_root"], "c.py")))
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

    def test_commit_head_ahead_unrelated_uses_review_snapshot(self):
        sha = self.later_commit_setup()
        self.write("c.py", "c = 1\n")
        head = self.commit("unrelated")
        data = self.ok("--commit", sha)
        self.assertEqual(data["head"], head)
        self.assertEqual(data["expected_head"], sha)
        self.assertEqual(self.read_review(data, "a.py"), "a = 2\n")
        self.assertEqual(self.read_review(data, "b.py"), "b = 2\n")
        self.assertFalse(os.path.exists(os.path.join(data["snapshot_root"], "c.py")))

    def test_commit_later_change_does_not_change_snapshot(self):
        sha = self.later_commit_setup()
        self.write("b.py", "b = 3\n")
        self.commit("changes b")
        data = self.ok("--commit", sha)
        self.assertFalse(data["checked_out"])
        self.assertNotEqual(data["head"], sha)
        self.assertEqual(self.read_review(data, "b.py"), "b = 2\n")
        self.assertEqual(self.read(os.path.join(self.repo, "b.py")), "b = 3\n")

    def test_commit_later_delete_still_has_review_file(self):
        sha = self.later_commit_setup()
        os.remove(os.path.join(self.repo, "b.py"))
        self.commit("deletes b")
        data = self.ok("--commit", sha)
        self.assertFalse(data["checked_out"])
        self.assertEqual(self.rel(data), ["a.py", "b.py"])
        self.assertEqual(self.read_review(data, "b.py"), "b = 2\n")

    def test_dirty_commit_at_head_uses_immutable_snapshot(self):
        self.init()
        self.write("a.py", "a = 1\n")
        sha = self.commit("under review")
        self.write("a.py", "a = 99\n")
        data = self.ok("--commit", sha)
        self.assertTrue(data["checked_out"])
        self.assertEqual(data["head"], sha)
        self.assertEqual(self.read_review(data, "a.py"), "a = 1\n")
        self.assertEqual(self.read(os.path.join(self.repo, "a.py")), "a = 99\n")
        self.write("a.py", "a = 100\n")
        self.assertEqual(self.read_review(data, "a.py"), "a = 1\n")
        self.assertEqual(self.read(os.path.join(self.repo, "a.py")), "a = 100\n")

    def test_snapshot_preserves_surrounding_helper_despite_later_change(self):
        self.init()
        self.write("app.py", "from helper import value\n")
        self.write("helper.py", "value = 'review'\n")
        self.commit("base")
        self.write("app.py", "from helper import value\nprint(value)\n")
        under_review = self.commit("change app")
        self.write("helper.py", "value = 'later'\n")
        self.commit("change surrounding helper")
        data = self.ok("--commit", under_review)
        self.assertEqual(self.rel(data), ["app.py"])
        self.assertEqual(data["change_count"], 1)
        self.assertEqual(
            self.read(os.path.join(data["snapshot_root"], "helper.py")),
            "value = 'review'\n",
        )
        self.assertEqual(self.read(os.path.join(self.repo, "helper.py")), "value = 'later'\n")

    def test_commit_diff_chunks_preserve_all_450_files(self):
        self.init()
        self.write("seed.py", "seed = True\n")
        self.commit("seed")
        expected = set()
        for i in range(450):
            rel = "m/f{:03d}.py".format(i)
            self.write(rel, "v = {}\n".format(i))
            expected.add(rel)
        sha = self.commit("many files")
        data = self.ok("--commit", sha)
        self.assertEqual(self.paths(data), sorted(expected))
        self.assertEqual(data["file_count"], 450)
        self.assertEqual(data["change_count"], 450)
        diff = self.read_diff(data)
        self.assertEqual(diff.count("diff --git "), 450)
        self.assertIn("+v = 0\n", diff)
        self.assertIn("+v = 449\n", diff)

    def test_shallow_boundary_parent_is_not_a_root_commit(self):
        self.init()
        self.write("a.py", "a = 1\n")
        self.commit("base")
        self.write("b.py", "b = 2\n")
        self.commit("reviewed change")
        shallow = os.path.join(self.tmp, "shallow")
        self.git(
            "clone", "--no-local", "--depth", "1", f"file://{self.repo}", "shallow",
            cwd=self.tmp,
        )

        error = self.fails("--commit", "HEAD", cwd=shallow)
        self.assertIn("parent", error.lower())
        self.assertIn("fetch", error.lower())

        self.git("fetch", "--deepen=1", "origin", cwd=shallow)
        data = self.ok("--commit", "HEAD", cwd=shallow)
        self.assertEqual(self.rel(data), ["b.py"])
        self.assertEqual(data["change_count"], 1)
        self.assertEqual(self.read_review(data, "b.py"), "b = 2\n")

    def test_snapshot_preserves_staged_repository_index(self):
        self.init()
        self.write("a.py", "a = 1\n")
        sha = self.commit("under review")
        self.write("staged.py", "staged = True\n")
        self.git("add", "staged.py")
        before = self.git("write-tree")

        data = self.ok("--commit", sha)
        self.assertEqual(self.git("write-tree"), before)
        self.assertFalse(os.path.exists(os.path.join(data["snapshot_root"], "staged.py")))

    def test_repository_root_with_trailing_space(self):
        self.repo = os.path.join(self.tmp, "repo ")
        os.makedirs(self.repo)
        self.init()
        self.write("a.py", "a = 1\n")
        self.commit("root")
        data = self.ok("--commit", "HEAD")
        self.assertEqual(data["repo_root"], os.path.realpath(self.repo))
        self.assertEqual(self.read_review(data, "a.py"), "a = 1\n")

    def test_root_commit(self):
        self.init()
        self.write("a.py", "root = True\n")
        root = self.commit("root")
        data = self.ok("--commit", root)
        self.assertEqual(self.rel(data), ["a.py"])
        self.assertEqual(data["change_count"], 1)
        self.assertEqual(self.entry(data, "a.py")["status"], "added")
        self.assertIn("+root = True", self.read_diff(data))
        self.assertEqual(self.read_review(data, "a.py"), "root = True\n")
        self.assertTrue(data["checked_out"])

    def test_snapshot_neutralizes_only_symlinks_that_escape(self):
        self.init()
        outside = self.write("outside.py", "outside_secret = True\n", root=self.tmp)
        self.write("real.py", "inside = True\n")
        os.symlink(outside, os.path.join(self.repo, "escape.py"))
        os.symlink("real.py", os.path.join(self.repo, "internal.py"))
        root = self.commit("root with symlinks")

        data = self.ok("--commit", root)
        self.assertEqual(data["file_count"], 3)
        self.assertEqual(data["change_count"], 3)
        escaped = os.path.join(data["snapshot_root"], "escape.py")
        internal = os.path.join(data["snapshot_root"], "internal.py")
        self.assertFalse(os.path.islink(escaped))
        self.assertEqual(self.read(escaped), outside)
        self.assertEqual(self.read_review(data, "escape.py"), outside)
        self.assertNotEqual(self.read(escaped), "outside_secret = True\n")
        self.assertEqual(
            os.path.commonpath([os.path.realpath(data["snapshot_root"]), os.path.realpath(escaped)]),
            os.path.realpath(data["snapshot_root"]),
        )
        self.assertTrue(os.path.islink(internal))
        self.assertEqual(
            os.path.realpath(internal),
            os.path.realpath(os.path.join(data["snapshot_root"], "real.py")),
        )
        self.assertEqual(self.read_review(data, "internal.py"), "inside = True\n")
        self.assertTrue(
            any(
                "symlink" in note.lower() and "1" in note
                and ("escape" in note.lower() or "outside" in note.lower())
                for note in data["notes"]
            ),
            data["notes"],
        )

    def test_snapshot_includes_export_ignore_tracked_source(self):
        self.init()
        self.write(".gitattributes", "included.py export-ignore\n")
        self.write("included.py", "included = True\n")
        root = self.commit("root with export-ignore")
        data = self.ok("--commit", root)
        self.assertEqual(self.read_review(data, "included.py"), "included = True\n")
        self.assertEqual(
            self.read(os.path.join(data["snapshot_root"], "included.py")),
            "included = True\n",
        )

    def test_deleted_file_has_manifest_entry_and_old_content_in_diff(self):
        self.init()
        self.write("a.py")
        self.write("b.py", "b = 'before deletion'\n")
        self.commit("first")
        os.remove(os.path.join(self.repo, "b.py"))
        self.write("a.py", "x = 3\n")
        sha = self.commit("delete b")
        data = self.ok("--commit", sha)
        self.assertEqual(self.rel(data), ["a.py"])
        self.assertEqual(data["change_count"], 2)
        deleted = self.entry(data, "b.py")
        self.assertEqual(deleted["status"], "deleted")
        self.assertTrue(deleted["in_scope"])
        self.assertIsNone(deleted["review_path"])
        self.assertIn("-b = 'before deletion'", self.read_diff(data))

    def test_commit_json_paths_survive_git_snapshot(self):
        self.init()
        self.write("seed.py", "seed = True\n")
        self.commit("base")
        newline = "src/line\nbreak.py"
        unicode_path = "src/caf\u00e9-\u96ea.py"
        self.write(newline, "newline_commit = True\n")
        self.write(unicode_path, "unicode_commit = True\n")
        sha = self.commit("unusual names")
        data = self.ok("--commit", sha)
        self.assertEqual(set(self.rel(data)), {newline, unicode_path})
        self.assertEqual(self.read_review(data, newline), "newline_commit = True\n")
        self.assertEqual(self.read_review(data, unicode_path), "unicode_commit = True\n")
        self.assertEqual(data["change_count"], 2)


FAKE_GH = textwrap.dedent("""\
    #!{python}
    import json, os, sys
    here = os.path.dirname(os.path.realpath(__file__))
    args = sys.argv[1:]
    with open(os.path.join(here, "args.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(args) + "\\n")
    if os.path.exists(os.path.join(here, "fail")):
        sys.stderr.write("GraphQL: Could not resolve to a PullRequest\\n")
        sys.exit(1)
    if args[:2] == ["pr", "view"] and "--json" in args:
        with open(os.path.join(here, "pr.json"), encoding="utf-8") as fh:
            sys.stdout.write(fh.read())
    else:
        sys.stderr.write("fake gh: unexpected args %r\\n" % (args,))
        sys.exit(1)
""")


class PrMode(Base):
    def fake_gh(self, head, base, fail=False):
        bindir = os.path.join(self.tmp, "fakebin")
        os.makedirs(bindir)
        gh = os.path.join(bindir, "gh")
        with open(gh, "w", encoding="utf-8") as fh:
            fh.write(FAKE_GH.format(python=sys.executable))
        os.chmod(gh, 0o755)
        with open(os.path.join(bindir, "pr.json"), "w", encoding="utf-8") as fh:
            json.dump({"headRefOid": head, "baseRefOid": base}, fh)
        if fail:
            open(os.path.join(bindir, "fail"), "w").close()
        env = dict(self.env)
        env["PATH"] = bindir + os.pathsep + env.get("PATH", "")
        return env

    def gh_calls(self):
        path = os.path.join(self.tmp, "fakebin", "args.jsonl")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def test_pr_uses_structured_refs_local_merge_base_and_snapshot(self):
        self.init()
        self.write("a.py", "a = 1\n")
        self.commit("merge base")
        self.git("checkout", "-q", "-b", "feature")
        self.write("a.py", "a = 2\n")
        self.write("helper.py", "helper = 'pr'\n")
        pr_head = self.commit("pr head")
        self.git("checkout", "-q", "main")
        self.write("base_only.py", "base_only = True\n")
        base_tip = self.commit("base moves")
        self.write("a.py", "dirty = True\n")

        env = self.fake_gh(pr_head, base_tip)
        data = self.ok("--pr", "7", env=env)
        self.assertEqual(data["mode"], "pr")
        self.assertEqual(data["pr"], 7)
        self.assertEqual(data["head"], base_tip)
        self.assertEqual(data["expected_head"], pr_head)
        self.assertFalse(data["checked_out"])
        self.assertEqual(self.rel(data), ["a.py", "helper.py"])
        self.assertEqual(data["change_count"], 2)
        self.assertEqual(self.read_review(data, "a.py"), "a = 2\n")
        self.assertEqual(self.read(os.path.join(data["snapshot_root"], "helper.py")), "helper = 'pr'\n")
        self.assertFalse(os.path.exists(os.path.join(data["snapshot_root"], "base_only.py")))
        self.assertEqual(self.read(os.path.join(self.repo, "a.py")), "dirty = True\n")
        diff = self.read_diff(data)
        self.assertIn("+a = 2", diff)
        self.assertIn("+helper = 'pr'", diff)
        self.assertNotIn("base_only", diff)

        calls = self.gh_calls()
        self.assertTrue(calls)
        self.assertTrue(all(call[:2] != ["pr", "diff"] for call in calls), calls)
        views = [call for call in calls if call[:2] == ["pr", "view"]]
        self.assertEqual(len(views), 1, calls)
        view = views[0]
        fields = view[view.index("--json") + 1].split(",")
        self.assertEqual(set(fields), {"headRefOid", "baseRefOid"})
        self.assertNotIn("--name-only", view)

    def test_pr_missing_local_objects_has_fetch_and_checkout_instruction(self):
        self.init()
        self.write("a.py")
        self.commit()
        env = self.fake_gh("f" * 40, "e" * 40)
        line = self.fails("--pr", "9", env=env)
        self.assertIn("fetch", line.lower())
        self.assertTrue("checkout" in line.lower() or "check out" in line.lower(), line)
        self.assertTrue(all(call[:2] != ["pr", "diff"] for call in self.gh_calls()))

    def test_pr_checked_out(self):
        self.init()
        self.write("a.py", "a = 1\n")
        base = self.commit("base")
        self.write("a.py", "a = 2\n")
        sha = self.commit("pr")
        env = self.fake_gh(sha, base)
        data = self.ok("--pr", "#8", env=env)
        self.assertTrue(data["checked_out"])
        self.assertEqual(self.read_review(data, "a.py"), "a = 2\n")

    def test_pr_gh_fails(self):
        self.init()
        self.write("a.py")
        sha = self.commit()
        env = self.fake_gh(sha, sha, fail=True)
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
