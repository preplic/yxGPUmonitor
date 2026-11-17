#!/usr/bin/python3
"""Enable Pages and a repository-scoped publisher key using existing Git auth."""

import argparse
import json
from pathlib import Path
import subprocess
import urllib.error
import urllib.request

from export_data import REPO
from network import direct_environment, github_ssh_command

REPOSITORY = "preplic/yxGPUmonitor"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def credential():
    result = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
        capture_output=True, text=True, timeout=60, env={**direct_environment(), "GIT_TERMINAL_PROMPT": "0"})
    values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    token = values.get("password")
    if result.returncode or not token:
        raise ValueError("Existing GitHub credential is unavailable; authenticate Git on this node first")
    return token


def api(token, path, method="GET", body=None, missing=False):
    request = urllib.request.Request("https://api.github.com" + path,
        data=json.dumps(body).encode() if body is not None else None, method=method,
        headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "yxGPUmonitor-publisher",
                 "Content-Type": "application/json"})
    try:
        with OPENER.open(request, timeout=40) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        if exc.code == 404 and missing:
            return None
        try:
            message = json.loads(exc.read()).get("message", "GitHub API rejected request")
        except (ValueError, OSError):
            message = "GitHub API rejected request"
        raise ValueError("GitHub HTTP %d: %s" % (exc.code, message.replace(token, "[redacted]"))) from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", action="store_true", help="Read deployment status only")
    parser.add_argument("--pages-only", action="store_true")
    args = parser.parse_args()
    token = credential()
    path = "/repos/" + REPOSITORY
    if args.status:
        pages = api(token, path + "/pages", missing=True)
        runs = api(token, path + "/actions/workflows/pages.yml/runs?per_page=3")
        print(json.dumps({"pages": {key: pages.get(key) for key in ("html_url", "status", "build_type")} if pages else None,
                          "runs": [{key: run.get(key) for key in ("id", "status", "conclusion", "html_url", "head_sha")}
                                   for run in runs.get("workflow_runs", [])]}, indent=2))
        return
    pages = api(token, path + "/pages", missing=True)
    if not pages:
        pages = api(token, path + "/pages", "POST", {"build_type": "workflow"})
    elif pages.get("build_type") != "workflow":
        pages = api(token, path + "/pages", "PUT", {"build_type": "workflow"})
    print("GitHub Pages enabled for Actions: " + pages.get("html_url", "https://preplic.github.io/yxGPUmonitor/"), flush=True)
    if not args.pages_only:
        private = Path.home() / ".config/yxgpu-monitor"
        private.mkdir(parents=True, exist_ok=True, mode=0o700)
        key = private / "github_deploy_key"
        if not key.exists():
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C",
                            "yxGPUmonitor encrypted publisher", "-f", str(key)], check=True)
        public = key.with_suffix(".pub").read_text().strip()
        existing = api(token, path + "/keys")
        if not any(item["key"].split()[:2] == public.split()[:2] for item in existing):
            api(token, path + "/keys", "POST", {"title": "yxGPUmonitor encrypted publisher",
                                                "key": public, "read_only": False})
        meta = api(token, "/meta")
        known_hosts = private / "github_known_hosts"
        known_hosts.write_text("".join("github.com " + value + "\n" for value in meta["ssh_keys"]))
        known_hosts.chmod(0o600)
        command = github_ssh_command()
        subprocess.run(["git", "config", "core.sshCommand", command], cwd=REPO, check=True)
        subprocess.run(["git", "config", "http.proxy", ""], cwd=REPO, check=True)
        subprocess.run(["git", "remote", "set-url", "--push", "origin",
                        "ssh://git@ssh.github.com:443/" + REPOSITORY + ".git"], cwd=REPO, check=True)
        print("Repository-scoped deploy key configured outside Git; SSH connects directly without an agent.", flush=True)
    api(token, path + "/actions/workflows/pages.yml/dispatches", "POST", {"ref": "main"})
    print("Pages deployment dispatched.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        raise SystemExit(str(exc))
