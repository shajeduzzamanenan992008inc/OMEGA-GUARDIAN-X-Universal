"""Reproducible local performance baseline.

This measures the shipped implementation on a synthetic repository. It is a
local baseline, not a production capacity benchmark: there is no network, no
Docker sandbox and one machine.

Usage:
    python _dev/tools/benchmark_baseline.py --files 500 --runs 3
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SOURCE_ROOT))


def build_repository(root: Path, files: int) -> None:
    packages = max(1, files // 50)
    for package in range(packages):
        directory = root / f"pkg_{package:03d}"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "__init__.py").write_text("", encoding="utf-8")
        for index in range(max(1, files // packages)):
            (directory / f"module_{index:03d}.py").write_text(
                f"def function_{index}(value):\n    return value + {index}\n",
                encoding="utf-8",
            )
    (root / "README.md").write_text("# synthetic baseline repository\n", encoding="utf-8")


def timed(function, repeats: int) -> list[float]:
    samples: list[float] = []
    for _ in range(repeats):
        started = time.perf_counter()
        function()
        samples.append(time.perf_counter() - started)
    return samples


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", type=int, default=500)
    parser.add_argument("--runs", type=int, default=3)
    arguments = parser.parse_args()

    from ogx.config import load_config
    from ogx.orchestrator import Orchestrator
    from ogx.patching import apply_patch, plan_patch, rollback_patch
    from ogx.scanner import scan_repository

    results: dict[str, object] = {}
    with tempfile.TemporaryDirectory(prefix="ogx-benchmark-") as temporary:
        workspace = Path(temporary)
        root = workspace / "project"
        root.mkdir()
        os.environ["OGX_HOME"] = str(workspace / "state")
        build_repository(root, arguments.files)
        config = load_config()

        tracemalloc.start()
        scan_samples = timed(lambda: scan_repository(root, config), arguments.runs)
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        report = scan_repository(root, config)
        results["scan"] = {
            "files_examined": report.files_examined,
            "median_seconds": statistics.median(scan_samples),
            "samples_seconds": scan_samples,
            "peak_tracemalloc_mib": peak / (1024 * 1024),
        }

        engine = Orchestrator(db_path=workspace / "state" / "workflows" / "orchestrator.db")
        workflow_id = engine.create(root, skip_stages=("edit", "test"))["workflow"]["workflow_id"]

        def run_workflow() -> None:
            workflow = engine.run(workflow_id)["workflow"]
            if workflow["state"] != "succeeded":
                raise RuntimeError(f"workflow state was {workflow['state']}")

        workflow_samples = timed(run_workflow, 1)
        results["workflow_without_edit_test"] = {
            "median_seconds": statistics.median(workflow_samples),
            "samples_seconds": workflow_samples,
            "stages": 5,
        }

        state_ids: list[str] = []

        def create_workflow(index: int) -> None:
            created = engine.create(
                root,
                stages=("inspect",),
                skip_stages=("inspect",),
                idempotency_key=f"benchmark-{index}",
            )
            state_ids.append(created["workflow"]["workflow_id"])

        create_samples = timed(lambda: create_workflow(len(state_ids)), 50)
        status_samples = timed(lambda: engine.status(state_ids[0]), 50)
        list_samples = timed(lambda: engine.list(limit=50), 20)
        results["workflow_state"] = {
            "create_median_seconds": statistics.median(create_samples),
            "status_median_seconds": statistics.median(status_samples),
            "list_50_median_seconds": statistics.median(list_samples),
            "workflow_rows": len(state_ids),
        }

        target = root / "pkg_000" / "module_000.py"
        target.write_text("VALUE = 1\n", encoding="utf-8")

        def patch_cycle() -> None:
            plan = plan_patch(
                root,
                [{"path": "pkg_000/module_000.py", "old": "VALUE = 1", "new": "VALUE = 2"}],
            )
            apply_patch(plan["plan_id"], approved=True, actor_subject="benchmark")
            rollback_patch(plan["plan_id"], approved=True, actor_subject="benchmark")

        patch_samples = timed(patch_cycle, arguments.runs)
        results["patch_plan_apply_rollback"] = {
            "median_seconds": statistics.median(patch_samples),
            "samples_seconds": patch_samples,
        }

    results["environment"] = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "machine": platform.machine(),
        "synthetic_files_requested": arguments.files,
        "runs": arguments.runs,
        "note": "Local synthetic baseline; no network, no container sandbox.",
    }
    print(json.dumps(results, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
