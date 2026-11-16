#!/usr/bin/python3
"""Reject plaintext monitoring JSON and the configured password before publishing."""

import argparse
import json
from pathlib import Path
import subprocess

from export_data import PRIVATE_CONFIG, REPO


def check(private_path=PRIVATE_CONFIG):
    private = json.loads(Path(private_path).expanduser().read_text())
    password = private["password"].encode()
    candidates = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=REPO).split(b"\0")
    checked = 0
    for candidate in candidates:
        if not candidate:
            continue
        path = REPO / candidate.decode()
        if path.is_file():
            if password in path.read_bytes():
                raise ValueError("Configured password found in a publishable file: " + str(path.relative_to(REPO)))
            checked += 1
    allowed = {"version", "algorithm", "kdf", "iterations", "compression", "salt", "iv", "ciphertext"}
    for path in (REPO / "site/data").rglob("*.json"):
        if set(json.loads(path.read_text())) != allowed:
            raise ValueError("Only encrypted envelopes are allowed under site/data: " + path.name)
    print("Public file check passed: %d files; no configured password or plaintext data JSON." % checked)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(PRIVATE_CONFIG))
    args = parser.parse_args()
    check(args.config)
