from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import export_data


class CollectorExportTests(unittest.TestCase):
    def build(self, private=None):
        cfg = {"host": "test-node", "gpu_root": "/unused/gpu",
               "expected_gpu_count": 8, "gpu_interval": 10}
        gpu_state = {"created_at": 100, "started_at": 900, "running": True,
                     "last_success_at": 995, "sample_count": 120, "interval_seconds": 10,
                     "ledger_id": "test-ledger", "users": {}}
        common = SimpleNamespace()
        gpu = SimpleNamespace(health=Mock(return_value=[]))
        modules = {"monitor_common": common, "gpu_usage": gpu}
        with patch.object(export_data, "monitor_paths", return_value=(cfg, "/unused/code")), \
                patch.object(export_data.sys, "path", []), \
                patch.object(export_data.importlib, "import_module", side_effect=modules.__getitem__), \
                patch.object(export_data, "read_json", return_value=gpu_state), \
                patch.object(export_data, "history", return_value=([], [])), \
                patch.object(export_data, "gpu_metrics", return_value=[]):
            return export_data.build_payload(private or {"allowed_users": ["test-account"]}, now=1000)

    def test_gpu_restart_keeps_ledger_origin_and_uses_current_run_start_time(self):
        payload = self.build()
        self.assertEqual(payload["node"]["startedAt"], 100)
        gpu = payload["collectors"]["gpu"]
        self.assertEqual(gpu["startedAt"], 900)
        self.assertTrue(gpu["running"])

    def test_export_needs_no_proxy_configuration_module_or_logs(self):
        payload = self.build()
        self.assertNotIn("audit", payload)
        self.assertEqual(set(payload["collectors"]), {"gpu"})
        self.assertTrue(all(item["source"] == "gpu" for item in payload["events"]))

    def test_node_sampling_and_website_publication_have_separate_intervals(self):
        payload = self.build()
        self.assertEqual(payload["publishInterval"], 900)
        self.assertEqual(payload["node"]["gpuInterval"], 10)
        self.assertEqual(payload["generatedAt"], 1000)
        self.assertEqual(payload["collectors"]["gpu"]["lastSample"], 995)

    def test_extra_users_preserve_existing_accounts_and_remove_duplicates(self):
        payload = self.build({"allowed_users": ["test-account"],
                                  "extra_allowed_users": ["extra-account", "test-account"]})
        self.assertEqual(payload["allowedUsers"], ["extra-account", "test-account"])

    def test_extra_users_preserve_automatic_linux_account_discovery(self):
        accounts = [SimpleNamespace(pw_uid=1001, pw_name="server-account", pw_shell="/bin/bash"),
                    SimpleNamespace(pw_uid=1002, pw_name="no-login", pw_shell="/bin/false")]
        with patch.object(export_data.pwd, "getpwall", return_value=accounts):
            payload = self.build({"extra_allowed_users": ["extra-account"]})
        self.assertEqual(payload["allowedUsers"], ["extra-account", "server-account"])


if __name__ == "__main__":
    unittest.main()
