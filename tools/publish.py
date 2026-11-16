#!/usr/bin/python3
"""Publish only the encrypted snapshot; never stage other local changes."""

import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import time

from check_public import check
from export_data import PRIVATE_CONFIG, REPO, export


def git(*args, output=False):
    result = subprocess.run(["git", *args], cwd=REPO, check=True, text=True,
                            capture_output=output, timeout=120,
                            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    return result.stdout.strip() if output else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(PRIVATE_CONFIG))
    parser.add_argument("--push", action="store_true")
    args = parser.parse_args()
    directory = Path.home() / ".local/state/yxgpu-monitor"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / "publish.lock").open("w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.push:
            if git("branch", "--show-current", output=True) != "main":
                raise ValueError("Publishing requires the main branch")
            if git("diff", "--cached", "--name-only", output=True):
                raise ValueError("Unrelated staged changes found; publish aborted")
            git("fetch", "origin", "main")
            git("merge", "--ff-only", "origin/main")
        result = export(args.config)
        check(args.config)
        print(json.dumps({"at": time.time(), **result}), flush=True)
        if args.push:
            path = "site/data/snapshot.enc.json"
            git("add", "--", path)
            staged = git("diff", "--cached", "--name-only", output=True)
            if staged and staged != path:
                raise ValueError("Unexpected staged files; refusing to commit")
            if staged:
                git("commit", "-m", "data: refresh encrypted monitoring snapshot")
            git("push", "origin", "HEAD:main")


if __name__ == "__main__":
    main()
