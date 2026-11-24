"""Regression: discovery-root persistence must work with the stdlib alone.

The package declares no runtime dependencies, but ``_save_roots_to_config``
used to import ``tomli_w``. On a clean install that made ``DiscoveryConfig``
fail with ModuleNotFoundError as soon as a root was added.
"""
from __future__ import annotations

import os
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

from ogx.discovery import DiscoveryConfig


class DiscoveryConfigTomlTests(unittest.TestCase):
    def test_adding_a_root_writes_parsable_stdlib_toml(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"
            target = Path(tmp) / "project"
            target.mkdir()
            with patch.dict(os.environ, {"OGX_HOME": str(state)}):
                config = DiscoveryConfig()
                config.roots = []
                config.add_root(
                    target,
                    name='Quoted "name" and \\ path',
                    max_depth=2,
                    exclusions=["build dir", "vendor"],
                )
                stored = state / "discovery.toml"
                self.assertTrue(stored.is_file(), "discovery roots were not persisted")
                data = tomllib.loads(stored.read_text(encoding="utf-8"))

        self.assertEqual(len(data["roots"]), 1)
        root = data["roots"][0]
        self.assertEqual(root["name"], 'Quoted "name" and \\ path')
        self.assertEqual(Path(root["path"]), target.resolve())
        self.assertTrue(root["enabled"])
        self.assertEqual(root["max_depth"], 2)
        self.assertEqual(root["exclusions"], ["build dir", "vendor"])

    def test_saved_config_has_no_third_party_import_at_call_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "project"
            target.mkdir()
            with patch.dict(os.environ, {"OGX_HOME": str(Path(tmp) / "state")}):
                config = DiscoveryConfig()
                config.roots = []
                # Would raise ModuleNotFoundError if tomli_w were still required.
                config.add_root(target, name="demo")
                self.assertTrue((Path(tmp) / "state" / "discovery.toml").is_file())


if __name__ == "__main__":
    unittest.main()
