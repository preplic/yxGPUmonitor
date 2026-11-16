import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import publish


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
