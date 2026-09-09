import tempfile
import threading
import time
import unittest
from datetime import date
from pathlib import Path

from shopline_monitor.dashboard_runtime import DashboardRuntime


def payload(value=10):
    return {"source": {"mode": "live", "syncedAt": "2026-09-07T09:00:00+08:00", "errors": []},
            "kpis": {"orders": {"value": value}}}


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.runtimes = []

    def tearDown(self):
        for runtime in self.runtimes:
            runtime.close()
        self.directory.cleanup()

    def runtime(self, builder, **kwargs):
        runtime = DashboardRuntime(builder, Path(self.directory.name), fingerprint="test", **kwargs)
        self.runtimes.append(runtime)
        return runtime

    def ready(self, runtime, **kwargs):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            result = runtime.request("1d", date(2026, 9, 7), {}, **kwargs)
            if not result["refresh"]["running"]:
                return result
            time.sleep(0.01)
        self.fail("background job did not complete")

    def test_pending_requests_return_immediately_and_share_one_job(self):
        release = threading.Event()
        calls = []

        def build(*args, **kwargs):
            calls.append(args)
            release.wait(3)
            return payload()

        runtime = self.runtime(build)
        start = time.monotonic()
        for _ in range(10):
            response = runtime.request("1d", date(2026, 9, 7), {}, force=True)
            self.assertTrue(response["pending"])
            self.assertTrue(response["refresh"]["running"])
        self.assertLess(time.monotonic() - start, 0.5)
        release.set()
        self.assertEqual(self.ready(runtime)["kpis"]["orders"]["value"], 10)
        self.assertEqual(len(calls), 1)

    def test_snapshot_survives_restart_and_query_keys_do_not_mix(self):
        runtime = self.runtime(lambda *args, **kwargs: payload())
        self.ready(runtime)
        restarted = self.runtime(lambda *args, **kwargs: self.fail("fresh snapshot should not refresh"))
        response = restarted.request("1d", date(2026, 9, 7), {})
        self.assertEqual(response["kpis"]["orders"]["value"], 10)
        self.assertTrue(response["source"]["cached"])
        self.assertNotEqual(runtime.query_key("1d", date(2026, 9, 7), {}), runtime.query_key("1d", date(2026, 9, 8), {}))
        self.assertNotEqual(runtime.query_key("1d", date(2026, 9, 7), {}), runtime.query_key("1d", date(2026, 9, 7), {"channel":"Yahoo"}))
        self.assertTrue(list(Path(self.directory.name).glob("*.json")))

    def test_refresh_failure_keeps_original_snapshot_and_timestamp(self):
        responses = [payload(), RuntimeError("upstream unavailable")]

        def build(*args, **kwargs):
            item = responses.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        runtime = self.runtime(build)
        original = self.ready(runtime)
        runtime.request("1d", date(2026, 9, 7), {}, force=True)
        failed = self.ready(runtime)
        self.assertEqual(failed["refresh"]["state"], "error")
        self.assertEqual(failed["source"]["syncedAt"], original["source"]["syncedAt"])
        self.assertEqual(failed["kpis"]["orders"]["value"], 10)

    def test_payload_with_integration_errors_does_not_replace_good_snapshot(self):
        responses = [payload(), {**payload(0), "source": {"mode":"error", "errors":["GA4 unavailable"]}}]
        runtime = self.runtime(lambda *args, **kwargs: responses.pop(0))
        self.ready(runtime)
        runtime.request("1d", date(2026, 9, 7), {}, force=True)
        self.assertEqual(self.ready(runtime)["kpis"]["orders"]["value"], 10)

    def test_bounded_jobs_do_not_start_an_unbounded_queue(self):
        release = threading.Event()
        runtime = self.runtime(lambda *args, **kwargs: (release.wait(3), payload())[1], max_jobs=1)
        runtime.request("1d", date(2026, 9, 7), {})
        response = runtime.request("1d", date(2026, 9, 8), {})
        self.assertEqual(response["refresh"]["state"], "queued")
        release.set()

    def test_corrupt_snapshot_does_not_break_refresh(self):
        runtime = self.runtime(lambda *args, **kwargs: payload())
        key = runtime.query_key("1d", date(2026, 9, 7), {})
        (Path(self.directory.name) / (key + ".json")).write_text("{broken", encoding="utf-8")
        self.assertEqual(self.ready(runtime)["kpis"]["orders"]["value"], 10)

    def test_ga4_failure_does_not_hide_new_shopline_orders(self):
        partial = payload(20)
        partial["source"]["errors"] = ["GA4 unavailable"]
        responses = [payload(10), partial]
        runtime = self.runtime(lambda *args, **kwargs: responses.pop(0))
        self.ready(runtime)
        runtime.request("1d", date(2026, 9, 7), {}, force=True)
        self.assertEqual(self.ready(runtime)["kpis"]["orders"]["value"], 20)
        restarted = self.runtime(lambda *args, **kwargs: self.fail("should load last complete snapshot"))
        self.assertEqual(self.ready(restarted)["kpis"]["orders"]["value"], 10)
