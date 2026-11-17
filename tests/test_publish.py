import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import publish
from network import direct_environment, github_ssh_command


class DirectTransportTests(unittest.TestCase):
    def test_fetch_does_not_inherit_proxies_or_forwarded_credentials(self):
        inherited = {"https_proxy": "http://127.0.0.1:9999", "HTTP_PROXY": "http://127.0.0.1:9999",
                     "ALL_PROXY": "socks5://127.0.0.1:9999", "SSH_AUTH_SOCK": "/tmp/forwarded-agent",
                     "GIT_SSH_COMMAND": "ssh -o ProxyCommand=broken-tunnel", "PATH": "/usr/bin"}
        with patch.dict(os.environ, inherited, clear=True), \
                patch.object(publish.subprocess, "run", return_value=Mock(stdout="ok\n")) as run:
            self.assertEqual(publish.git("fetch", "origin", "main", output=True), "ok")
        args, kwargs = run.call_args
        self.assertEqual(args[0][-3:], ["fetch", "origin", "main"])
        self.assertIn("http.proxy=", args[0])
        environment = kwargs["env"]
        for name in ("https_proxy", "HTTP_PROXY", "ALL_PROXY", "SSH_AUTH_SOCK"):
            self.assertNotIn(name, environment)
        self.assertEqual(environment["PATH"], "/usr/bin")
        self.assertEqual(environment["GIT_TERMINAL_PROMPT"], "0")
        command = shlex.split(environment["GIT_SSH_COMMAND"])
        self.assertEqual(command[1:3], ["-F", "/dev/null"])
        self.assertIn("IdentityAgent=none", command)
        self.assertIn("BatchMode=yes", command)
        self.assertNotIn("ProxyCommand=broken-tunnel", command)

    def test_direct_environment_preserves_unrelated_settings(self):
        with patch.dict(os.environ, {"no_proxy": "*", "KEEP_ME": "value"}, clear=True):
            self.assertEqual(direct_environment(), {"KEEP_ME": "value"})

    def test_background_key_is_stored_on_node_instead_of_an_ssh_session(self):
        with patch("network.Path.home", return_value=Path("/node/account")):
            command = shlex.split(github_ssh_command())
        self.assertEqual(command[command.index("-i") + 1], "/node/account/.config/yxgpu-monitor/github_deploy_key")
        self.assertIn("UserKnownHostsFile=/node/account/.config/yxgpu-monitor/github_known_hosts", command)


class PublicationWindowTests(unittest.TestCase):
    def test_retry_keeps_remote_snapshot_as_boundary_after_failed_push(self):
        previous = {"ciphertext": "previous-successful-publication"}
        pushes = 0

        def git(*args, **kwargs):
            nonlocal pushes
            if args == ("branch", "--show-current"):
                return "main"
            if args == ("show", "origin/main:site/data/snapshot.enc.json"):
                return json.dumps(previous)
            if args == ("push", "origin", "HEAD:main"):
                pushes += 1
                if pushes == 1:
                    raise subprocess.CalledProcessError(1, ["git", *args])
            return ""

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(publish.Path, "home", return_value=Path(directory)), \
                patch.object(sys, "argv",
                             ["publish.py", "--config", "/unused/private.json", "--push"]), \
                patch.object(publish, "git", side_effect=git), \
                patch.object(publish, "check"), \
                patch.object(publish, "export", return_value={}) as export, \
                patch("builtins.print"):
            with self.assertRaises(subprocess.CalledProcessError):
                publish.main()
            publish.main()
        self.assertEqual(pushes, 2)
        self.assertEqual(export.call_count, 2)
        for call in export.call_args_list:
            self.assertEqual(call.args, ("/unused/private.json",))
            self.assertEqual(call.kwargs, {"previous_snapshot": previous})


if __name__ == "__main__":
    unittest.main()
