import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from ogx.cli import main
from ogx.runtime.observe import ErrorTracker


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


class GuardCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["OGX_HOME"] = str(Path(self.tmp.name) / "state")
        self.addCleanup(lambda: os.environ.pop("OGX_HOME", None))

    def test_check_reports_defaults_and_warnings(self):
        code, out, _ = run("guard", "check")
        self.assertEqual(code, 0)
        self.assertIn("built-in secure defaults", out)
        self.assertIn('"signature_profile": "balanced"', out)
        cfg = Path(self.tmp.name) / "monitor.toml"
        cfg.write_text('[firewall]\nmode = "monitor"\n', encoding="utf-8")
        code, out, _ = run("guard", "check", "--config", str(cfg), "--strict")
        self.assertEqual(code, 1)
        self.assertIn("WARNING: mode=monitor", out)

    def test_invalid_config_is_a_clean_error(self):
        cfg = Path(self.tmp.name) / "bad.toml"
        cfg.write_text('[firewall]\nmode = "yolo"\n', encoding="utf-8")
        code, _, err = run("guard", "check", "--config", str(cfg))
        self.assertEqual(code, 2)
        self.assertIn("Guard failed", err)

    def test_test_command_exit_codes_and_decision_json(self):
        code, out, _ = run("guard", "test", "/search?q=shoes")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["action"], "allow")
        code, out, _ = run("guard", "test", "/s?q=<script>alert(1)</script>")
        decision = json.loads(out)
        self.assertEqual((code, decision["action"], decision["rule_id"]), (1, "block", "OGX-RT-1020"))
        code, out, _ = run("guard", "test", "/api", "--method", "POST", "--header", "Content-Type: application/json",
                           "--body", '{"u":"x\' or 1=1 -- "}')
        self.assertEqual((code, json.loads(out)["rule_id"]), (1, "OGX-RT-1010"))
        code, _, err = run("guard", "test", "/x", "--header", "broken header")
        self.assertEqual(code, 2)

    def test_keygen_prints_a_key_once_and_only_the_hash_for_config(self):
        code, out, _ = run("guard", "keygen", "--name", "svc", "--role", "admin")
        self.assertEqual(code, 0)
        key = out.split("\n")[1].strip()
        self.assertGreaterEqual(len(key), 40)
        self.assertEqual(out.count(key), 1)                                  # the plain key appears exactly once
        self.assertIn('sha256 = "', out)
        self.assertEqual(run("guard", "keygen", "--name", "bad name")[0], 2)

    def test_keygen_output_round_trips_through_the_config(self):
        from ogx.runtime.access import authenticate
        from ogx.runtime.config import load_runtime_config
        _, out, _ = run("guard", "keygen", "--name", "svc", "--role", "admin")
        key = out.split("\n")[1].strip()
        toml = "[access]\nenabled = true\n" + out[out.index("[[access.keys]]"):out.index("\n\n", out.index("[[access.keys]]"))] + "\n"
        cfg_path = Path(self.tmp.name) / "runtime.toml"
        cfg_path.write_text(toml, encoding="utf-8")
        cfg = load_runtime_config(cfg_path)
        self.assertEqual(authenticate(cfg.api_keys, key).role, "admin")
        self.assertIsNone(authenticate(cfg.api_keys, key + "x"))


class ObserveCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["OGX_HOME"] = str(Path(self.tmp.name) / "state")
        self.addCleanup(lambda: os.environ.pop("OGX_HOME", None))

    def test_snapshot_and_empty_logs(self):
        code, out, _ = run("observe", "snapshot")
        self.assertEqual(code, 0)
        self.assertIn("python", json.loads(out))
        code, out, _ = run("observe", "logs")
        self.assertEqual(code, 0)
        self.assertIn("No log events", out)

    def test_errors_list_show_clear(self):
        db = str(Path(self.tmp.name) / "errors.db")
        tracker = ErrorTracker(db)
        try:
            raise ValueError("password=hunter2 failed")
        except ValueError as exc:
            fp = tracker.capture(exc, context={"path": "/x"})
        code, out, _ = run("observe", "errors", "--db", db, "list")
        self.assertEqual(code, 0)
        self.assertIn(fp, out)
        self.assertNotIn("hunter2", out)
        code, out, _ = run("observe", "errors", "--db", db, "show", fp)
        self.assertEqual(code, 0)
        self.assertIn("ValueError", out)
        self.assertEqual(run("observe", "errors", "--db", db, "show", "nope")[0], 1)
        self.assertEqual(run("observe", "errors", "--db", db, "clear")[0], 2)
        self.assertEqual(run("observe", "errors", "--db", db, "clear", "--confirm")[0], 0)
        self.assertIn("No tracked errors", run("observe", "errors", "--db", db, "list")[1])


if __name__ == "__main__":
    unittest.main()
