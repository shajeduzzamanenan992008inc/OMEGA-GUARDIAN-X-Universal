from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ogx.intelligence import AnalysisOptions, ProjectIntelligenceEngine
from ogx.intelligence.detectors.project_type import classify_project
from ogx.intelligence.models import (
    ArchitectureIndicator, Confidence, at_least, strongest,
)
from ogx.intelligence.options import ANALYZER_NAMES
from ogx.intelligence.reports import (
    render_json, render_markdown, render_terminal, report_dict,
)


class IntelligenceModelTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _analyze(self, **options):
        return ProjectIntelligenceEngine().analyze(
            self.root, AnalysisOptions(registry_lookup=False, **options))

    def test_empty_project_is_honest_and_serializable(self):
        report = self._analyze()
        data = report.to_dict()
        self.assertEqual(data["metadata"]["schema_version"], "ogx-intelligence-1")
        self.assertEqual(data["metadata"]["analysis_mode"], "static-read-only")
        self.assertFalse(data["metadata"]["execution_performed"])
        self.assertFalse(data["metadata"]["network_access"])
        self.assertEqual(data["classification"]["kind"], "unknown project")
        self.assertEqual(data["classification"]["confidence"], "unknown")
        self.assertEqual(data["languages"], [])
        self.assertEqual(data["dependencies"]["declarations"], [])
        self.assertIsNone(data["health"]["score"])
        self.assertFalse(data["testing"]["executed"])
        self.assertTrue(data["metadata"]["analysis_id"].startswith("ogx-intel-"))
        self.assertIn("summary", data)
        serialized = json.dumps(data)
        self.assertNotIn("Confidence.", serialized)
        self.assertNotIn("HealthStatus.", serialized)
        self.assertEqual(report_dict(report), data)

        terminal = render_terminal(report)
        self.assertIn("Project Intelligence Report", terminal)
        self.assertIn("Execution performed: False", terminal)
        markdown = render_markdown(report)
        self.assertIn("not a security", markdown.lower())
        self.assertIn("No project code", markdown)

        parsed = json.loads(render_json(report))
        self.assertEqual(parsed["metadata"]["schema_version"], "ogx-intelligence-1")

    def test_confidence_ordering_and_classification(self):
        self.assertEqual(strongest(Confidence.POSSIBLE, Confidence.CONFIRMED,
                                   Confidence.UNKNOWN), Confidence.CONFIRMED)
        self.assertTrue(at_least(Confidence.LIKELY, Confidence.POSSIBLE))
        self.assertFalse(at_least(Confidence.POSSIBLE, Confidence.LIKELY))
        classification = classify_project([
            ArchitectureIndicator("web application", Confidence.CONFIRMED, "test"),
            ArchitectureIndicator("library", Confidence.LIKELY, "test"),
        ])
        self.assertEqual(classification.kind, "web application")
        self.assertEqual(classification.confidence.value, "confirmed")
        self.assertIn("library", classification.secondary_kinds)

    def test_options_are_bounded_and_excludes_extend_defaults(self):
        with self.assertRaises(ValueError):
            AnalysisOptions(max_files=0)
        with self.assertRaises(ValueError):
            AnalysisOptions(max_depth=21)
        with self.assertRaises(ValueError):
            AnalysisOptions(max_manifest_bytes=1)
        with self.assertRaises(ValueError):
            AnalysisOptions(analyzers=("does-not-exist",))
        options = AnalysisOptions(excludes=("custom-cache",), max_files=10)
        config = options.scan_config()
        self.assertEqual(config.max_files, 10)
        self.assertIn("custom-cache", config.excludes)
        self.assertIn(".git", config.excludes)
        self.assertEqual(options.selected_analyzers(), ANALYZER_NAMES)
        self.assertIn("registry_lookup", options.to_dict())

    def test_report_sections_filter_output(self):
        (self.root / "app.py").write_text("print('ok')\n", encoding="utf-8")
        report = self._analyze()
        language_view = render_terminal(report, ["languages"])
        self.assertIn("Languages", language_view)
        self.assertNotIn("Engineering health", language_view)
        health_view = render_terminal(report, ["health"])
        self.assertIn("Engineering health", health_view)
        self.assertNotIn("Testing and quality", health_view)
        markdown = render_markdown(report, ["health"])
        self.assertIn("## Engineering health", markdown)
        self.assertNotIn("## Languages", markdown)

    def test_analysis_does_not_modify_the_project(self):
        (self.root / "README.md").write_text("# Demo\n", encoding="utf-8")
        (self.root / "app.py").write_text("print('safe')\n", encoding="utf-8")
        before = {}
        for path in sorted(self.root.rglob("*")):
            if path.is_file():
                before[path.relative_to(self.root).as_posix()] = path.read_bytes()
        report = self._analyze()
        after = {}
        for path in sorted(self.root.rglob("*")):
            if path.is_file():
                after[path.relative_to(self.root).as_posix()] = path.read_bytes()
        self.assertEqual(before, after)
        self.assertEqual(report.metadata.execution_performed, False)

    def test_analyzer_failure_is_isolated(self):
        (self.root / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
        with patch("ogx.intelligence.engine.analyze_dependencies",
                   side_effect=RuntimeError("synthetic failure")):
            report = ProjectIntelligenceEngine().analyze(
                self.root,
                AnalysisOptions(analyzers=("language", "dependencies"),
                                registry_lookup=False),
            )
        self.assertTrue(any("dependencies" in item for item in report.errors))
        self.assertEqual(report.metadata.analyzers, ["language"])
        self.assertTrue(report.languages)
        self.assertIn("synthetic failure", " ".join(report.errors))

    def test_report_recomputation_is_not_cached(self):
        (self.root / "app.py").write_text("print('one')\n", encoding="utf-8")
        first = self._analyze()
        second = self._analyze()
        self.assertEqual(first.metadata.cache_status, "recomputed")
        self.assertEqual(first.metadata.analysis_id, second.metadata.analysis_id)
        self.assertEqual(first.project.project_id, second.project.project_id)


if __name__ == "__main__":
    unittest.main()
