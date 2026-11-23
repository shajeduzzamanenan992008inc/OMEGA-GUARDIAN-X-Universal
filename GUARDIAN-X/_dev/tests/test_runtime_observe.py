import asyncio
import io
import json
import logging
import os
import tempfile
import threading
import unittest
from pathlib import Path

from ogx.runtime.config import from_mapping
from ogx.runtime.engine import Shield, log_sink
from ogx.runtime.observe import (ErrorTracker, ErrorTrackingASGI, ErrorTrackingMiddleware, Timer, configure,
                                 dump_threads, get_logger, install_excepthooks, log_context, read_events, snapshot, timed)
from ogx.runtime.observe import logs as logs_module
from ogx.runtime.request import Request


def reset_logging():
    for name in ("omega", ""):
        logs_module._remove_managed(logging.getLogger(name))


class LoggingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(reset_logging)
        self.path = Path(self.tmp.name) / "logs" / "omega.jsonl"

    def test_json_lines_are_structured_redacted_and_carry_context(self):
        configure(self.path, "INFO")
        with log_context(request_id="r-123", password="hunter2"):
            get_logger("app").info("login failed token=abc123secret", extra={"fields": {"authorization": "Bearer xyz", "user": "bob"}})
        event = json.loads(self.path.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual((event["level"], event["logger"]), ("INFO", "omega.app"))
        self.assertIn("token=<redacted>", event["message"])
        self.assertNotIn("abc123secret", json.dumps(event))
        self.assertEqual(event["ctx"]["request_id"], "r-123")
        self.assertEqual(event["ctx"]["password"], "<redacted>")
        self.assertEqual(event["fields"], {"authorization": "<redacted>", "user": "bob"})
        self.assertTrue(event["ts"].endswith("+00:00"))

    def test_root_logger_is_untouched_unless_requested(self):
        before = list(logging.getLogger().handlers)
        configure(self.path, "INFO")
        self.assertEqual(logging.getLogger().handlers, before)
        configure(self.path, "INFO", capture_root=True)
        self.assertEqual(len(logging.getLogger().handlers), len(before) + 1)
        reset_logging()
        self.assertEqual(logging.getLogger().handlers, before)

    def test_configure_is_idempotent_and_levels_filter(self):
        configure(self.path, "WARNING")
        configure(self.path, "WARNING")
        managed = [h for h in logging.getLogger("omega").handlers if getattr(h, "_omega_managed", False)]
        self.assertEqual(len(managed), 1)
        get_logger("x").info("quiet")
        get_logger("x").warning("loud")
        self.assertEqual([e["message"] for e in read_events(self.path)], ["loud"])
        with self.assertRaises(ValueError):
            configure(self.path, "NOPE")

    def test_exceptions_are_logged_with_redacted_traceback(self):
        configure(self.path, "INFO")
        try:
            raise ValueError("bad password=topsecret")
        except ValueError:
            get_logger("x").exception("boom")
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("ValueError", text)
        self.assertNotIn("topsecret", text)

    def test_read_events_limit_and_level(self):
        configure(self.path, "DEBUG")
        for i in range(10):
            get_logger("x").log(logging.WARNING if i % 2 else logging.INFO, f"m{i}")
        self.assertEqual(len(read_events(self.path, limit=3)), 3)
        self.assertTrue(all(e["level"] == "WARNING" for e in read_events(self.path, limit=50, min_level="WARNING")))
        self.assertEqual(read_events(Path(self.tmp.name) / "missing.jsonl"), [])

    def test_default_path_lives_in_the_state_directory(self):
        os.environ["OGX_HOME"] = self.tmp.name
        try:
            self.assertEqual(logs_module.default_log_path(), Path(self.tmp.name).absolute() / "logs" / "omega.jsonl")
        finally:
            os.environ.pop("OGX_HOME", None)

    def test_guard_events_flow_into_the_central_log_without_query_strings(self):
        configure(self.path, "INFO")
        shield = Shield(from_mapping({"ratelimit": {"enabled": False}}))     # default sink = log_sink
        shield.inspect(Request("GET", "/search", "q=<script>alert(1)</script>&token=abc123", [("Host", "x")], b"", "203.0.113.9"))
        event = json.loads(self.path.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(event["logger"], "omega.guard")
        self.assertEqual(event["fields"]["rule_id"], "OGX-RT-1020")
        self.assertEqual(event["fields"]["action"], "block")
        self.assertNotIn("abc123", json.dumps(event))
        self.assertNotIn("script", json.dumps(event["fields"]))


class ErrorTrackerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tracker = ErrorTracker(Path(self.tmp.name) / "errors.db", max_events=3)

    @staticmethod
    def _raise(kind=ValueError, message="boom"):
        try:
            raise kind(message)
        except Exception as exc:
            return exc

    def test_same_raise_site_groups_together_different_sites_do_not(self):
        a1, a2 = self._raise(message="id 41 missing"), self._raise(message="id 99 missing")
        fp1, fp2 = self.tracker.capture(a1), self.tracker.capture(a2)
        self.assertEqual(fp1, fp2)
        try:
            {}["k"]
        except KeyError as exc:
            fp3 = self.tracker.capture(exc, context={"path": "/x"})
        self.assertNotEqual(fp1, fp3)
        groups = {g["fingerprint"]: g for g in self.tracker.groups()}
        self.assertEqual(groups[fp1]["count"], 2)
        self.assertEqual(len(groups), 2)

    def test_secrets_are_redacted_in_messages_stacks_and_context(self):
        exc = self._raise(message="login failed password=hunter2 for https://bob:pw123@db.example/x")
        fp = self.tracker.capture(exc, context={"authorization": "Bearer abc", "path": "/login"})
        detail = self.tracker.group(fp)
        blob = json.dumps(detail)
        for secret in ("hunter2", "pw123", "Bearer abc"):
            self.assertNotIn(secret, blob)
        self.assertEqual(detail["recent"][0]["context"]["path"], "/login")

    def test_event_retention_is_bounded_and_clear_works(self):
        for _ in range(10):
            self.tracker.capture(self._raise())
        fp = self.tracker.groups()[0]["fingerprint"]
        self.assertEqual(self.tracker.group(fp)["count"], 10)
        self.assertLessEqual(len(self.tracker.group(fp, events=50)["recent"]), 3)
        self.assertEqual(self.tracker.clear(), 1)
        self.assertEqual(self.tracker.groups(), [])

    def test_capture_never_raises(self):
        bad = ErrorTracker(Path(self.tmp.name) / "e2.db")
        bad.path = Path(self.tmp.name) / "no" / "such" / "dir" / "x.db"
        self.assertEqual(bad.capture(self._raise()), "")

    def test_wsgi_middleware_records_and_reraises(self):
        def boom(environ, start_response):
            raise RuntimeError("wsgi failure")

        def streaming(environ, start_response):
            start_response("200 OK", [])
            yield b"first"
            raise OSError("mid-stream failure")

        for app, kind in ((boom, "RuntimeError"), (streaming, "OSError")):
            wrapped = ErrorTrackingMiddleware(app, self.tracker)
            environ = {"REQUEST_METHOD": "GET", "PATH_INFO": "/p", "omega.request_id": "rid-1"}
            with self.assertRaises((RuntimeError, OSError)):
                list(wrapped(environ, lambda *a, **k: None))
            self.assertTrue(any(g["type"].endswith(kind) for g in self.tracker.groups()))

    def test_asgi_middleware_records_and_reraises(self):
        async def boom(scope, receive, send):
            raise ValueError("asgi failure")

        wrapped = ErrorTrackingASGI(boom, self.tracker)
        with self.assertRaises(ValueError):
            asyncio.run(wrapped({"type": "http", "method": "GET", "path": "/a", "state": {"omega_request_id": "r9"}}, None, None))
        group = self.tracker.group(self.tracker.groups()[0]["fingerprint"])
        self.assertEqual(group["recent"][0]["context"]["request_id"], "r9")

    def test_excepthooks_capture_and_restore(self):
        import sys
        previous = sys.excepthook
        uninstall = install_excepthooks(self.tracker)
        try:
            self.assertIsNot(sys.excepthook, previous)
            exc = self._raise(RuntimeError, "thread death")
            done = threading.Event()
            sys.excepthook, original = (lambda *a: done.set()), sys.excepthook
            sys.excepthook = original
        finally:
            uninstall()
        self.assertIs(sys.excepthook, previous)


class DebugTests(unittest.TestCase):
    def test_snapshot_exposes_env_names_never_values(self):
        os.environ["OMEGA_TEST_SECRET"] = "supersecretvalue"
        try:
            data = snapshot()
            blob = json.dumps(data)
            self.assertIn("OMEGA_TEST_SECRET", data["omega_env_names"])
            self.assertNotIn("supersecretvalue", blob)
            for key in ("python", "platform", "pid", "threads", "state_dir"):
                self.assertIn(key, data)
        finally:
            os.environ.pop("OMEGA_TEST_SECRET", None)

    def test_timer_and_timed_decorator(self):
        with Timer("x") as t:
            sum(range(1000))
        self.assertGreaterEqual(t.ms, 0.0)
        calls = []

        @timed(label="adder")
        def add(a, b):
            calls.append(1)
            return a + b
        self.assertEqual(add(2, 3), 5)
        self.assertEqual(calls, [1])

    def test_dump_threads_lists_the_current_thread(self):
        text = dump_threads()
        self.assertIn(threading.current_thread().name, text)
        self.assertIn("test_dump_threads_lists_the_current_thread", text)


if __name__ == "__main__":
    unittest.main()
