#!/usr/bin/env python3
"""Red-team-specific public CLI tests for the standalone resolver copy.

The generic resolver behavior lives in bug-hunt's suite. These tests pin the
exact red-team extension/name invocation and its profile, manifest, and source
coverage without importing anything from another skill.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPT = os.path.join(os.path.dirname(os.path.realpath(__file__)), "resolve_target.py")
PROFILE = ".red-team-profile.md"
EXTS = (
    "ts,tsx,js,jsx,mjs,cjs,mts,cts,py,go,rs,rb,java,swift,kt,"
    "c,cc,cpp,cxx,h,hh,hpp,hxx,tf,yaml,yml,json,toml,xml,sql,cs,php,"
    "sh,bash,zsh,fish,ps1,vue,svelte,graphql,proto"
)
NAMES = (
    "Dockerfile",
    "Makefile",
    "go.mod",
    "go.sum",
    "requirements.txt",
    "Gemfile",
    "Gemfile.lock",
    "Cargo.lock",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "Pipfile",
    "Pipfile.lock",
    "composer.lock",
    ".env",
    ".env.example",
    ".env.local",
    ".env.production",
)


def clean_env(tmp):
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


class RedTeamInvocation(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="red-team-resolver-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.env = clean_env(self.tmp)
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)

    def git(self, *args):
        proc = subprocess.run(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", *args],
            cwd=self.repo, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        return proc.stdout.decode().strip()

    def init(self):
        self.git("init", "-q")
        self.git("symbolic-ref", "HEAD", "refs/heads/main")

    def write(self, rel, text):
        path = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def read(self, path):
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")

    def run_script(self, *target):
        # Keep this command shape identical to red-team's documented invocation.
        cmd = [sys.executable, "-I", SCRIPT, "--profile-name", PROFILE, "--ext", EXTS]
        for name in NAMES:
            cmd.extend(("--name", name))
        cmd.extend(("--", *target))
        return subprocess.run(
            cmd, cwd=self.repo, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

    def ok(self, *target):
        proc = self.run_script(*target)
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
        data["_entries"] = manifest["entries"]
        readable = [
            entry for entry in manifest["entries"]
            if entry["in_scope"] and entry["review_path"] is not None
        ]
        self.assertEqual(data["file_count"], len(readable))
        for entry in readable:
            self.assertTrue(os.path.isfile(entry["review_path"]), entry)
            self.assertEqual(
                os.path.commonpath([data["assessment_root"], entry["review_path"]]),
                data["assessment_root"],
            )
        if data["mode"] == "path":
            self.assertIsNone(data["snapshot_root"])
            self.assertIsNone(data["change_count"])
        else:
            self.assertEqual(data["snapshot_root"], data["assessment_root"])
            self.assertEqual(
                data["change_count"],
                sum(1 for entry in manifest["entries"] if entry["in_scope"]),
            )
        return data

    def entry(self, data, path):
        matches = [entry for entry in data["_entries"] if entry["path"] == path]
        self.assertEqual(len(matches), 1, (path, data["_entries"]))
        return matches[0]

    def paths(self, data):
        return {entry["path"] for entry in data["_entries"]}

    def readable_paths(self, data):
        return {
            entry["path"] for entry in data["_entries"]
            if entry["in_scope"] and entry["review_path"] is not None
        }

    def test_exact_invocation_covers_production_extensions_names_and_profile(self):
        expected = set()
        contents = {}
        for index, ext in enumerate(EXTS.split(",")):
            rel = "src/module_{:02d}.{}".format(index, ext)
            text = "extension {} marker\n".format(ext)
            self.write(rel, text)
            expected.add(rel)
            contents[rel] = text
        for name in NAMES:
            rel = "config/" + name
            text = "exact name {} marker\n".format(name)
            self.write(rel, text)
            expected.add(rel)
            contents[rel] = text
        self.write("src/README.md", "not source\n")
        self.write("src/module.test.ts", "not a production test target\n")
        self.write("node_modules/pkg/index.js", "third party\n")
        self.write(PROFILE, "# red-team test profile\n")

        data = self.ok(self.repo)
        self.assertEqual(data["mode"], "path")
        self.assertEqual(data["target"], self.repo)
        self.assertEqual(data["assessment_root"], self.repo)
        self.assertEqual(data["profile"], os.path.join(self.repo, PROFILE))
        self.assertEqual(self.paths(data), expected)
        self.assertEqual(self.readable_paths(data), expected)
        self.assertEqual(data["file_count"], len(expected))
        for rel, text in contents.items():
            self.assertEqual(self.read(self.entry(data, rel)["review_path"]), text)

    def test_exact_invocation_diff_manifest_and_snapshot_cover_new_surfaces(self):
        self.init()
        self.write("old.rb", "legacy = 'removed'\n")
        self.write("keep.go", "package main\n")
        self.commit("base")
        self.git("checkout", "-q", "-b", "feature")
        os.remove(os.path.join(self.repo, "old.rb"))
        changed = {
            "api.tsx": "export const api = 'committed';\n",
            "infra.tf": "resource \"example\" \"this\" {}\n",
            "schema.graphql": "type Query { health: String }\n",
            "config/go.mod": "module example.invalid/assessment\n",
        }
        for rel, text in changed.items():
            self.write(rel, text)
        self.write("notes.md", "out of scope diff marker\n")
        self.commit("production surfaces")
        self.write("api.tsx", "export const api = 'dirty';\n")

        data = self.ok("--diff")
        self.assertEqual(data["mode"], "diff")
        self.assertEqual(data["profile"], None)
        self.assertEqual(
            self.paths(data),
            {"api.tsx", "infra.tf", "schema.graphql", "config/go.mod", "notes.md", "old.rb"},
        )
        self.assertEqual(self.readable_paths(data), set(changed))
        self.assertEqual(data["file_count"], len(changed))
        self.assertEqual(data["change_count"], len(changed) + 1)
        deleted = self.entry(data, "old.rb")
        self.assertEqual(deleted["status"], "deleted")
        self.assertTrue(deleted["in_scope"])
        self.assertIsNone(deleted["review_path"])
        self.assertFalse(self.entry(data, "notes.md")["in_scope"])
        for rel, text in changed.items():
            self.assertEqual(self.read(self.entry(data, rel)["review_path"]), text)
        self.assertEqual(self.read(os.path.join(data["snapshot_root"], "keep.go")), "package main\n")
        self.assertEqual(self.read(os.path.join(self.repo, "api.tsx")), "export const api = 'dirty';\n")
        diff = self.read(data["diff_file"])
        self.assertIn("export const api = 'committed';", diff)
        self.assertIn("resource \"example\" \"this\"", diff)
        self.assertIn("type Query { health: String }", diff)
        self.assertIn("-legacy = 'removed'", diff)
        self.assertNotIn("out of scope diff marker", diff)


if __name__ == "__main__":
    unittest.main(verbosity=2)
