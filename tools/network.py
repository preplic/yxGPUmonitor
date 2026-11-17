"""Direct networking and node-local GitHub authentication for background publishing."""

import os
from pathlib import Path
import shlex


UNSET_ENVIRONMENT = ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY",
                     "no_proxy", "NO_PROXY", "GIT_PROXY_COMMAND", "GIT_SSH", "GIT_SSH_COMMAND", "SSH_AUTH_SOCK")


def direct_environment():
    excluded = {name.lower() for name in UNSET_ENVIRONMENT}
    return {name: value for name, value in os.environ.items() if name.lower() not in excluded}


def github_ssh_command():
    private = Path.home() / ".config/yxgpu-monitor"
    return shlex.join(["/usr/bin/ssh", "-F", "/dev/null", "-i", str(private / "github_deploy_key"),
                       "-o", "IdentitiesOnly=yes", "-o", "IdentityAgent=none", "-o", "BatchMode=yes",
                       "-o", "HostKeyAlias=github.com", "-o", "StrictHostKeyChecking=yes",
                       "-o", "UserKnownHostsFile=" + str(private / "github_known_hosts"),
                       "-o", "ConnectTimeout=20"])
