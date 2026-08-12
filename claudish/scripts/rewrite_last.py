#!/usr/bin/env python3
"""Rewrite the latest assistant message through a local/LAN Ollama endpoint."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request


def fail(message: str) -> None:
    print(f"Claudish could not rewrite the previous response: {message}")
    raise SystemExit(0)


def find_transcript(session_id: str) -> Path:
    root = Path.home() / ".claude" / "projects"
    matches = list(root.rglob(f"{session_id}.jsonl"))
    if not matches:
        fail(f"transcript {session_id}.jsonl was not found under {root}")
    return max(matches, key=lambda path: path.stat().st_mtime)


def extract_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    chunks: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str):
                chunks.append(text)
    return "\n".join(chunks)


def latest_assistant_message(path: Path) -> str:
    latest = ""
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            message = event.get("message")
            if not isinstance(message, dict):
                continue
            if event.get("type") == "assistant" or message.get("role") == "assistant":
                candidate = extract_text(message.get("content"))
                if candidate.strip():
                    latest = candidate
    if not latest:
        fail("no assistant message was found in the current transcript")
    return latest


def rewrite(source: str) -> str:
    base_url = os.getenv("CLAUDISH_OLLAMA", "http://localhost:11434").rstrip("/")
    model = os.getenv("CLAUDISH_MODEL", "gemma4:12b")
    timeout = float(os.getenv("CLAUDISH_TIMEOUT", "120"))
    system = (
        "Rewrite the assistant message so a busy colleague can read it once and get it. "
        "Rules: short sentences, everyday words, active voice. Cut filler and hedging, "
        "but preserve every fact, qualification, name, number, command, URL, and file path. "
        "Keep the original markdown structure (headings, lists, tables) and leave fenced "
        "code blocks byte-for-byte unchanged. Keep necessary technical terms; don't dumb "
        "down code or commands. Do not answer the message, add commentary, or explain "
        "your changes. Start your output with the first word of the rewritten message — "
        "no preamble like 'Here is the rewrite'."
    )
    body = json.dumps(
        {
            "model": model,
            "stream": False,
            "think": False,
            "options": {"temperature": 0.2},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": source},
            ],
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/api/chat",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        fail(f"Ollama returned HTTP {exc.code}")
    except (urllib.error.URLError, TimeoutError) as exc:
        fail(f"cannot reach Ollama at {base_url}: {exc}")
    except (json.JSONDecodeError, ValueError) as exc:
        fail(f"Ollama returned invalid JSON: {exc}")

    if result.get("error"):
        fail(str(result["error"]))
    message = result.get("message")
    output = message.get("content", "") if isinstance(message, dict) else ""
    if not isinstance(output, str) or not output.strip():
        fail("Ollama returned an empty response")
    return output.strip()


def main() -> None:
    if len(sys.argv) != 2 or not sys.argv[1].strip():
        fail("Claude Code did not provide a session ID")
    transcript = find_transcript(sys.argv[1].strip())
    print(rewrite(latest_assistant_message(transcript)))


if __name__ == "__main__":
    main()
