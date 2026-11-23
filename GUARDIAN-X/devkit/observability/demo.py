"""Self-contained demo of logs + error tracking + debug utilities (writes only to a temp folder)."""
import runpy
import tempfile
from pathlib import Path

shield = runpy.run_path(str(Path(__file__).resolve().parents[2] / "init.py"))["load_runtime"]()
obs = shield.observe

with tempfile.TemporaryDirectory() as tmp:
    log_path = obs.configure(Path(tmp) / "demo.jsonl", "INFO", console=True)
    log = obs.get_logger("demo")
    with obs.log_context(request_id="demo-1"):
        log.info("user login token=abc123 ok", extra={"fields": {"user": "bob", "password": "hunter2"}})
    tracker = obs.ErrorTracker(Path(tmp) / "errors.db")
    try:
        {}["missing"]
    except KeyError as exc:
        fingerprint = tracker.capture(exc, context={"path": "/demo"})
    print("tracked error:", tracker.groups()[0]["type"], "x", tracker.groups()[0]["count"], "fingerprint", fingerprint)
    print("last log event:", obs.read_events(log_path, 1)[0])
    print("snapshot keys:", sorted(obs.snapshot())[:6], "...")
