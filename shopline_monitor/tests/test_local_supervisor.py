import logging
import os
import queue
import subprocess
import sys
import threading
import unittest
from unittest.mock import Mock

from shopline_monitor.local_supervisor import monitor_child, probe_health, stop_child


class SupervisorTests(unittest.TestCase):
    def test_three_consecutive_health_failures_stop_only_the_owned_child(self):
        child = Mock()
        child.poll.return_value = None
        stop = Mock()
        health = Mock(return_value=False)
        result = monitor_child(child, health, stop, logging.getLogger("test"), sleep=lambda _: None)
        self.assertEqual(result, "unhealthy")
        self.assertEqual(health.call_count, 3)
        stop.assert_called_once_with(child)

    def test_success_resets_failure_count(self):
        child = Mock()
        child.poll.return_value = None
        health = Mock(side_effect=[False, False, True, False, False, False])
        stop = Mock()
        monitor_child(child, health, stop, logging.getLogger("test"), sleep=lambda _: None)
        self.assertEqual(health.call_count, 6)
        stop.assert_called_once_with(child)

    def test_exited_child_does_not_kill_other_processes(self):
        child = Mock()
        child.poll.return_value = 1
        stop = Mock()
        health = Mock()
        self.assertEqual(monitor_child(child, health, stop, logging.getLogger("test")), "exited")
        stop.assert_not_called()
        health.assert_not_called()

    def test_unresponsive_real_http_child_is_terminated(self):
        script = """
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        time.sleep(30)
server = HTTPServer(('127.0.0.1', 0), Handler)
print(server.server_address[1], flush=True)
server.serve_forever()
"""
        child = subprocess.Popen(
            [sys.executable, "-u", "-c", script], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        ready = queue.Queue()
        reader = threading.Thread(target=lambda: ready.put(child.stdout.readline()), daemon=True)
        reader.start()
        try:
            port = int(ready.get(timeout=5))
            result = monitor_child(child, lambda: probe_health(port), stop_child,
                                   logging.getLogger("test"), interval=0.01)
            self.assertEqual(result, "unhealthy")
            self.assertIsNotNone(child.poll())
        finally:
            stop_child(child)
            child.stdout.close()
