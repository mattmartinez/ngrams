"""Unused helper retained only as an unreachable-sink benchmark trap."""

import subprocess


def legacy_shell(command):
    return subprocess.run(command, shell=True, check=True, capture_output=True)
