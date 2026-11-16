from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import export_data


class CollectorExportTests(unittest.TestCase):
    def build(self, proxy_state):
        cfg = {"host": "test-node", "gpu_root": "/unused/gpu", "proxy_root": "/unused/proxy",
               "expected_gpu_count": 8, "gpu_interval": 10}
        gpu_state = {"created_at": 100, "started_at": 900, "running": True,
                     "last_success_at": 995, "sample_count": 120, "interval_seconds": 10,
                     "ledger_id": "test-ledger", "users": {}}
        audit = {"incidents": [], "since": 0, "until": 1000, "last_success_at": 950,
                 "problems": [], "ports": [1234], "ignore_uids": [],
                 "coverage_seconds": 900, "coverage": [], "gaps": [], "connections": []}
        common = SimpleNamespace(load_json=Mock(return_value=proxy_state))
        gpu = SimpleNamespace(health=Mock(return_value=[]))
        proxy = SimpleNamespace(report=Mock(return_value=audit),
                                health=Mock(return_value=[] if proxy_state else ["no status"]))
        modules = {"monitor_common": common, "gpu_usage": gpu, "bci-proxy-audit": proxy}
        with patch.object(export_data, "monitor_paths", return_value=(cfg, "/unused/code")), \
                patch.object(export_data.sys, "path", []), \
                patch.object(export_data.importlib, "import_module", side_effect=modules.__getitem__), \
                patch.object(export_data, "read_json", return_value=gpu_state), \
                patch.object(export_data, "history", return_value=([], [])), \
                patch.object(export_data, "gpu_metrics", return_value=[]):
            return export_data.build_payload({"allowed_users": ["test-account"]}, now=1000)

    def test_restart_keeps_ledger_origin_and_uses_independent_service_start_times(self):
        payload = self.build({"started_at": 800, "stopped_at": 960, "running": False,
                              "last_success_at": 950, "sample_count": 2400, "interval_seconds": 0.5})
        self.assertEqual(payload["node"]["startedAt"], 100)
        gpu, proxy = payload["collectors"]["gpu"], payload["collectors"]["proxy"]
        self.assertEqual(gpu["startedAt"], 900)
        self.assertTrue(gpu["running"])
        self.assertEqual(proxy["startedAt"], 800)
        self.assertFalse(proxy["running"])
        self.assertEqual(proxy["stoppedAt"], 960)
        self.assertEqual(proxy["lastSample"], 950)
        self.assertEqual(proxy["intervalSeconds"], 0.5)
        self.assertEqual(proxy["sampleCount"], 2400)

    def test_missing_proxy_status_remains_unknown(self):
        proxy = self.build({})["collectors"]["proxy"]
        self.assertIsNone(proxy["running"])
        self.assertIsNone(proxy["startedAt"])
        self.assertIsNone(proxy["lastSample"])
        self.assertEqual(proxy["problems"], ["no status"])


if __name__ == "__main__":
    unittest.main()
