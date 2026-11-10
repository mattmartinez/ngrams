#!/usr/bin/env python3
"""Tests for sanitize.py. Run: python3 -I test_sanitize.py

Every secret below is fake and is built by joining pieces, so secret scanners
do not flag this file.
"""

import os
import subprocess
import sys
import tempfile
import unittest

SCRIPT = os.path.join(os.path.dirname(os.path.realpath(__file__)), "sanitize.py")

AWS_ID = "AKIA" + "ABCDEFGHIJKLMNOP"
GH_TOKEN = "ghp_" + "a1B2" * 9
SLACK_TOKEN = "xoxb-" + "1234567890-abcdefghij"
SK_KEY = "sk-" + "abc123XYZ_" * 3
JWT = "eyJ" + "hbGciOiJIUzI1NiJ9" + "." + "eyJ" + "zdWIiOiIxMjM0In0" + "." + "c2lnbmF0dXJl"
PEM = (
    "-----BEGIN RSA " + "PRIVATE KEY-----\n"
    "MIIBOgIBAAJBAKj34GkxFhD90vcNLYLInFEX\n"
    "-----END RSA " + "PRIVATE KEY-----"
)


def run(stdin_text=None, args=()):
    proc = subprocess.run(
        [sys.executable, "-I", SCRIPT, *args],
        input=None if stdin_text is None else stdin_text.encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")


class SanitizeTest(unittest.TestCase):
    def check(self, text, secret, marker):
        rc, out, err = run(text)
        self.assertEqual(rc, 0, err)
        self.assertNotIn(secret, out)
        self.assertIn(marker, out)
        self.assertNotIn(secret, err, "secret value leaked into the log")
        return out

    def test_private_key_block(self):
        out = self.check(f"before\n{PEM}\nafter\n", "MIIBOgIBAAJBAKj34", "[REDACTED:private-key-block]")
        self.assertIn("before", out)
        self.assertIn("after", out)

    def test_url_credentials_keep_host(self):
        out = self.check(
            "dsn postgres://admin:" + "hunter2pass" + "@db.example.invalid:5432/app\n",
            "hunter2pass",
            "[REDACTED:url-credentials]",
        )
        self.assertIn("postgres://", out)
        self.assertIn("@db.example.invalid:5432/app", out)
        self.assertNotIn("admin", out)

    def test_authorization_header(self):
        value = "dXNlcjpwYXNzd29yZA=="
        out = self.check(f"Authorization: Basic {value}\n", value, "[REDACTED:authorization-header]")
        self.assertIn("Authorization: Basic ", out)

    def test_bare_bearer_token(self):
        value = "abcDEF123456" + "ghiJKL7890"
        self.check(f"curl -H 'X-Auth: Bearer {value}'\n", value, "[REDACTED:bearer-token]")

    def test_jwt(self):
        self.check(f"session {JWT} ok\n", JWT, "[REDACTED:jwt]")

    def test_aws_access_key_id(self):
        self.check(f"using {AWS_ID} for upload\n", AWS_ID, "[REDACTED:aws-access-key-id]")

    def test_github_token(self):
        self.check(f"remote uses {GH_TOKEN}\n", GH_TOKEN, "[REDACTED:github-token]")

    def test_slack_token(self):
        self.check(f"hook {SLACK_TOKEN}\n", SLACK_TOKEN, "[REDACTED:slack-token]")

    def test_sk_api_key(self):
        self.check(f"client({SK_KEY})\n", SK_KEY, "[REDACTED:sk-api-key]")

    def test_env_assignment(self):
        out = self.check("DB_PASSWORD=s3cr3t-value\n", "s3cr3t-value", "[REDACTED:assignment]")
        self.assertIn("DB_PASSWORD=", out)

    def test_json_assignment_keeps_quotes(self):
        out = self.check('{"api_key": "my quoted value", "n": 3}\n', "my quoted value", "[REDACTED:assignment]")
        self.assertIn('"api_key": "[REDACTED:assignment]"', out)
        self.assertIn('"n": 3', out)

    def test_yaml_assignment(self):
        self.check("client_secret: 'abc def'\n", "abc def", "[REDACTED:assignment]")

    def test_email(self):
        self.check("owner is someone@example.invalid today\n", "someone@example.invalid", "[REDACTED:email]")

    def test_facts_survive(self):
        text = "2026-01-02T03:04:05Z GET /v1/items 429 retry_after=30 latency_ms=812 count=17\n"
        rc, out, err = run(text)
        self.assertEqual(rc, 0, err)
        self.assertEqual(out, text)
        self.assertIn("done: 0 replaced", err)

    def test_specific_marker_is_not_counted_twice(self):
        rc, out, err = run(f"GITHUB_TOKEN={GH_TOKEN}\n")
        self.assertEqual(rc, 0, err)
        self.assertNotIn(GH_TOKEN, out)
        self.assertIn("rule github-token: 1 replaced", err)
        self.assertIn("rule assignment: 0 replaced", err)

    def test_log_counts_per_rule(self):
        rc, out, err = run(f"a {AWS_ID} b {AWS_ID}\n")
        self.assertEqual(rc, 0, err)
        self.assertIn("rule aws-access-key-id: 2 replaced", err)
        self.assertIn("done: 2 replaced", err)

    def test_reads_files_in_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = os.path.join(tmp, "a.log")
            second = os.path.join(tmp, "b.log")
            with open(first, "w") as handle:
                handle.write("first line\n")
            with open(second, "w") as handle:
                handle.write(f"key {AWS_ID}\n")
            rc, out, err = run(args=(first, second))
        self.assertEqual(rc, 0, err)
        self.assertEqual(out, "first line\nkey [REDACTED:aws-access-key-id]\n")

    def test_missing_file_exits_2_with_no_output(self):
        rc, out, err = run(args=("/no/such/file-zz.log",))
        self.assertEqual(rc, 2)
        self.assertEqual(out, "")
        self.assertIn("cannot read input", err)

    def test_invalid_utf8_does_not_crash(self):
        proc = subprocess.run(
            [sys.executable, "-I", SCRIPT],
            input=b"ok \xff\xfe bytes\n",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        self.assertIn(b"ok ", proc.stdout)


if __name__ == "__main__":
    unittest.main()
