from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import export_data


class CollectorExportTests(unittest.TestCase):
    def build(self, private=None, previous_generated_at=None, cards=None, averages=None,
              occupancy=None, current=None):
        cfg = {"host": "test-node", "gpu_root": "/unused/gpu",
               "expected_gpu_count": 8, "gpu_interval": 10}
        gpu_state = {"created_at": 100, "started_at": 900, "running": True,
                     "last_success_at": 995, "sample_count": 120, "interval_seconds": 10,
                     "ledger_id": "test-ledger", "users": {}, "current": current or {}}
        common = SimpleNamespace()
        gpu = SimpleNamespace(health=Mock(return_value=[]))
        modules = {"monitor_common": common, "gpu_usage": gpu}
        with patch.object(export_data, "monitor_paths", return_value=(cfg, "/unused/code")), \
                patch.object(export_data.sys, "path", []), \
                patch.object(export_data.importlib, "import_module", side_effect=modules.__getitem__), \
                patch.object(export_data, "read_json", return_value=gpu_state), \
                patch.object(export_data, "history", return_value=([], [], averages or {}, occupancy or {})), \
                patch.object(export_data, "gpu_metrics", return_value=cards or []):
            return export_data.build_payload(private or {"allowed_users": ["test-account"]}, now=1000,
                                             previous_generated_at=previous_generated_at)

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

    def test_metrics_use_actual_previous_update_time_instead_of_fixed_fifteen_minutes(self):
        payload = self.build(previous_generated_at=75)
        self.assertEqual(payload["node"]["metricWindow"], {"start": 75, "end": 1000})

    def test_previous_update_in_the_future_is_rejected(self):
        with self.assertRaises(ValueError):
            self.build(previous_generated_at=1001)

    def test_missing_samples_do_not_fall_back_to_instantaneous_metrics(self):
        cards = [{"uuid": "g", "model": "test GPU", "memoryUsed": 999, "utilization": 99}]
        payload = self.build(cards=cards)
        card = payload["node"]["gpus"][0]
        self.assertIsNone(card["memoryUsed"])
        self.assertIsNone(card["memoryUtilization"])
        self.assertIsNone(card["utilization"])
        self.assertEqual(card["metricSamples"], 0)

    def test_average_metrics_replace_publication_instant_values(self):
        cards = [{"uuid": "g", "model": "test GPU", "memoryUsed": 999, "utilization": 99}]
        averages = {"g": {"memoryUsed": 200, "memoryUtilization": 20, "utilization": 30}}
        card = self.build(cards=cards, averages=averages)["node"]["gpus"][0]
        self.assertEqual(card["memoryUsed"], 200)
        self.assertEqual(card["memoryUtilization"], 20)
        self.assertEqual(card["utilization"], 30)

    def test_interval_users_are_preserved_when_the_card_is_currently_idle(self):
        owners = [{"uid": 1001, "name": "alice"}, {"uid": 1002, "name": "bob"}]
        occupancy = {"g": {"intervalOwners": owners, "intervalOccupied": True}}
        card = self.build(cards=[{"uuid": "g", "model": "test GPU"}],
                          occupancy=occupancy)["node"]["gpus"][0]
        self.assertEqual(card["intervalOwners"], owners)
        self.assertTrue(card["intervalOccupied"])
        self.assertEqual(card["owners"], [])
        self.assertFalse(card["occupied"])
        self.assertEqual(card["processes"], 0)

    def test_current_users_remain_separate_from_interval_users(self):
        current = {"owners": {"g": {"1002:bob": {"uid": 1002, "username": "bob"}}},
                   "processes": [{"gpu_uuid": "g"}]}
        card = self.build(cards=[{"uuid": "g", "model": "test GPU"}],
                          current=current)["node"]["gpus"][0]
        self.assertEqual(card["owners"], [{"uid": 1002, "name": "bob"}])
        self.assertTrue(card["occupied"])
        self.assertEqual(card["intervalOwners"], [])
        self.assertFalse(card["intervalOccupied"])

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


class AverageMetricTests(unittest.TestCase):
    def observation(self, t, util, used, uuid="g"):
        return t, [{"uuid": uuid, "memory_total": 1000, "memory_used": used, "utilization": util}]

    def average(self, observations, start=100, end=130):
        return export_data.averaged_metrics(observations, start, end, 10)["g"]

    def test_time_weighted_average_clips_both_window_boundaries(self):
        values = [self.observation(95, 20, 100), self.observation(110, 80, 400),
                  self.observation(125, 0, 1000), self.observation(135, 100, 0)]
        result = self.average(values)
        self.assertAlmostEqual(result["utilization"], (20 * 10 + 80 * 15) / 30)
        self.assertAlmostEqual(result["memoryUsed"], (100 * 10 + 400 * 15 + 1000 * 5) / 30)
        self.assertAlmostEqual(result["memoryUtilization"], (10 * 10 + 40 * 15 + 100 * 5) / 30)
        self.assertEqual(result["metricSamples"], 3)
        self.assertEqual(result["metricCoverage"]["utilization"], 30)

    def test_missing_fields_do_not_count_as_zero_but_real_zero_is_valid(self):
        values = [self.observation(100, 0, None), self.observation(110, None, 400)]
        result = self.average(values, end=120)
        self.assertEqual(result["utilization"], 0)
        self.assertEqual(result["memoryUsed"], 400)
        self.assertEqual(result["memoryUtilization"], 40)
        self.assertEqual(result["metricCoverage"], {"utilization": 10, "memoryUsed": 10, "memoryUtilization": 10})

    def test_sampling_gap_is_excluded_from_the_denominator(self):
        values = [self.observation(100, 100, 1000), self.observation(200, 20, 200)]
        result = self.average(values, end=210)
        self.assertEqual(result["utilization"], 20)
        self.assertEqual(result["memoryUsed"], 200)
        self.assertEqual(result["metricCoverage"]["utilization"], 10)

    def test_known_stop_does_not_extend_the_last_value_to_the_end(self):
        result = self.average([self.observation(100, 80, 600), (105, [])], end=120)
        self.assertEqual(result["metricCoverage"]["utilization"], 5)
        self.assertEqual(result["utilization"], 80)

    def test_old_log_format_does_not_add_zero_metrics(self):
        values = [(100, [{"uuid": "g"}]), self.observation(110, 60, 300)]
        result = self.average(values, end=120)
        self.assertEqual(result["utilization"], 60)
        self.assertEqual(result["memoryUtilization"], 30)
        self.assertEqual(result["metricSamples"], 1)

    def test_duplicate_status_and_event_observation_is_counted_once(self):
        observation = self.observation(100, 30, 200)
        result = self.average([observation, observation], end=110)
        self.assertEqual(result["metricSamples"], 1)
        self.assertEqual(result["metricCoverage"]["memoryUsed"], 10)

    def test_invalid_values_are_missing_and_cards_are_grouped_by_uuid(self):
        values = [self.observation(100, float("nan"), None), self.observation(110, 200, None),
                  self.observation(115, 75, 400, uuid="other")]
        result = export_data.averaged_metrics(values, 100, 120, 10)
        self.assertIsNone(result["g"]["utilization"])
        self.assertIsNone(result["g"]["memoryUsed"])
        self.assertEqual(result["other"]["utilization"], 75)


class IntervalOccupancyTests(unittest.TestCase):
    def observation(self, t, name, uid=1001, uuid="g"):
        current = {"owners": {uuid: {str(uid): {"uid": uid, "username": name}}},
                   "processes": [{"gpu_uuid": uuid}]}
        return t, current

    def occupancy(self, observations, start=100, end=130):
        return export_data.interval_occupancy(observations, start, end, 10)

    def test_sequential_users_are_deduplicated_even_if_the_card_ends_idle(self):
        first = self.observation(100, "alice")
        values = [self.observation(110, "bob", uid=1002), first,
                  self.observation(115, "alice"), (120, {}), first]
        result = self.occupancy(values)["g"]
        self.assertEqual(result["intervalOwners"],
                         [{"uid": 1001, "name": "alice"}, {"uid": 1002, "name": "bob"}])
        self.assertTrue(result["intervalOccupied"])

    def test_users_outside_the_latest_update_window_are_excluded(self):
        values = [self.observation(80, "old"), (90, {}), self.observation(100, "alice"),
                  self.observation(130, "bob", uid=1002), self.observation(131, "future")]
        names = [owner["name"] for owner in self.occupancy(values)["g"]["intervalOwners"]]
        self.assertEqual(names, ["alice", "bob"])

    def test_recent_predecessor_carries_into_window_until_next_observation(self):
        result = self.occupancy([self.observation(95, "alice"), (105, {})])["g"]
        self.assertEqual(result["intervalOwners"], [{"uid": 1001, "name": "alice"}])

    def test_stale_predecessor_does_not_carry_across_a_sampling_gap(self):
        self.assertEqual(self.occupancy([self.observation(95, "old"), (130, {})]), {})

    def test_stop_or_error_before_window_terminates_predecessor(self):
        self.assertEqual(self.occupancy([self.observation(95, "old"), (99, {})]), {})

    def test_observed_user_inside_window_counts_despite_a_later_long_gap(self):
        result = self.occupancy([self.observation(110, "alice"), (200, {})], end=210)
        self.assertEqual(result["g"]["intervalOwners"], [{"uid": 1001, "name": "alice"}])

    def test_unattributed_processes_and_uid_fallback_are_supported(self):
        values = [self.observation(100, None),
                  (110, {"processes": [{"gpu_uuid": "unknown"}]})]
        result = self.occupancy(values)
        self.assertEqual(result["g"]["intervalOwners"], [{"uid": 1001, "name": "uid:1001"}])
        self.assertEqual(result["unknown"], {"intervalOwners": [], "intervalOccupied": True})

    def test_history_collects_raw_samples_instead_of_downsampled_trend_buckets(self):
        def sample(t, current, ledger="test"):
            return {"type": "sample", "time": t, "last_success_at": t, "current": current,
                    "ledger_id": ledger, "users": {}, "observed_physical_seconds": 0,
                    "unobserved_physical_seconds": 0, "unattributed_physical_seconds": 0}
        events = [sample(*self.observation(90, "old")), sample(*self.observation(100, "alice")),
                  sample(*self.observation(105, "other-ledger"), ledger="other"),
                  sample(*self.observation(110, "bob", uid=1002)), sample(120, {})]
        common = SimpleNamespace(read_events=Mock(return_value=events))
        cfg = {"gpu_root": "/unused", "host": "test", "gpu_interval": 10}
        trends, _, _, occupancy = export_data.history(cfg, sample(130, {}), common, 135, 100)
        self.assertLess(len(trends), len(events))
        self.assertEqual(occupancy["g"]["intervalOwners"],
                         [{"uid": 1001, "name": "alice"}, {"uid": 1002, "name": "bob"}])


if __name__ == "__main__":
    unittest.main()
