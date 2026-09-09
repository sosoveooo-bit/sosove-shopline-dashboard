import asyncio
import os
import unittest
from datetime import date
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as dashboard_app


class ContainerApiTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"DASHBOARD_ACCESS_TOKEN": "test-access", "VERCEL": ""})
        self.environment.start()
        self.client = TestClient(dashboard_app.app)
        self.headers = {"X-Dashboard-Token": "test-access"}

    def tearDown(self):
        self.client.close()
        self.environment.stop()

    def test_health_and_static_are_public_but_metrics_require_access(self):
        self.assertTrue(self.client.get("/api/health").json()["ok"])
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/api/metrics?background=1").status_code, 401)
        self.assertEqual(self.client.get("/static/.env").status_code, 404)

    def test_metrics_passes_background_query_to_shared_runtime(self):
        with patch.object(dashboard_app, "dashboard_response", return_value={"pending": True}) as build:
            response = self.client.get("/api/metrics?range=1d&date=2026-09-07&background=1&channel=SmartPush", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["pending"])
        self.assertTrue(build.call_args.kwargs["background"])
        self.assertEqual(build.call_args.kwargs["today"], date(2026, 9, 7))
        self.assertEqual(build.call_args.kwargs["filters"]["channel"], "SmartPush")

    def test_serverless_keeps_a_blocking_response_contract(self):
        with patch.dict(os.environ, {"VERCEL": "1"}), patch.object(dashboard_app, "dashboard_response", return_value={}) as build:
            self.client.get("/api/metrics?background=1", headers=self.headers)
        self.assertFalse(build.call_args.kwargs["background"])

    def test_sync_is_offloaded_so_health_is_not_blocked(self):
        def build(*args, **kwargs):
            with self.assertRaises(RuntimeError):
                asyncio.get_running_loop()
            return {"pending": True}

        with patch.object(dashboard_app, "dashboard_response", side_effect=build) as call:
            response = self.client.post("/api/sync", json={"range": "1d", "background": True}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(call.call_args.kwargs["force"])
        self.assertTrue(call.call_args.kwargs["background"])

    def test_viewer_cannot_force_refresh(self):
        with patch.dict(os.environ, {"DASHBOARD_ROLE": "viewer"}):
            response = self.client.post("/api/sync", json={"background": True}, headers=self.headers)
        self.assertEqual(response.status_code, 403)
