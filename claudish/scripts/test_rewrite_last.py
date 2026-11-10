#!/usr/bin/env python3
"""Tests for rewrite_last.py. Standard library only; never calls Ollama."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import rewrite_last  # noqa: E402

SESSION_ID = "11111111-2222-3333-4444-555555555555"


def write_transcript(config_dir: Path) -> Path:
    project = config_dir / "projects" / "-tmp-some-project"
    project.mkdir(parents=True)
    path = project / f"{SESSION_ID}.jsonl"
    events = [
        {"type": "user", "message": {"role": "user", "content": "hello"}},
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "first answer"}],
            },
        },
        {"type": "user", "message": {"role": "user", "content": "again"}},
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "name": "Bash", "input": {}},
                    {"type": "text", "text": "last answer"},
                ],
            },
        },
    ]
    with path.open("w", encoding="utf-8") as stream:
        for event in events:
            stream.write(json.dumps(event) + "\n")
        stream.write("not json\n")
    return path


class RewriteLastTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        # Fail loudly if any test reaches the network code path.
        patcher = mock.patch.object(
            rewrite_last, "rewrite", side_effect=AssertionError("rewrite() must not be called")
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_find_transcript_uses_claude_config_dir(self) -> None:
        config_dir = self.base / "config"
        expected = write_transcript(config_dir)
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(config_dir)}):
            self.assertEqual(rewrite_last.find_transcript(SESSION_ID), expected)

    def test_latest_assistant_message_returns_last(self) -> None:
        path = write_transcript(self.base / "config")
        self.assertEqual(rewrite_last.latest_assistant_message(path), "last answer")

    def test_find_transcript_defaults_to_home_claude(self) -> None:
        home = self.base / "home"
        expected = write_transcript(home / ".claude")
        env = {key: value for key, value in os.environ.items() if key != "CLAUDE_CONFIG_DIR"}
        env["HOME"] = str(home)
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(rewrite_last.find_transcript(SESSION_ID), expected)

    def test_empty_claude_config_dir_falls_back_to_home(self) -> None:
        home = self.base / "home"
        expected = write_transcript(home / ".claude")
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": "", "HOME": str(home)}):
            self.assertEqual(rewrite_last.find_transcript(SESSION_ID), expected)


if __name__ == "__main__":
    unittest.main()
