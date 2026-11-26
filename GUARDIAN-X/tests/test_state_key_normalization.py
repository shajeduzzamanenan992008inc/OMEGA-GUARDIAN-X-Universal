"""State-key normalization: one state directory per project, regardless of case.

Regression coverage for the Linux GitHub Actions failure in which
``alpha/project`` and ``alpha/PROJECT`` produced two sibling directories,
``project-<digest>`` and ``PROJECT-<digest>``, under ``<OGX_HOME>/projects``.

The digest was already computed from ``str(path.resolve()).casefold()``; the
human-readable slug that prefixes it kept the original letter case. On a
case-insensitive filesystem (Windows, macOS default) the two names are the same
directory, so the defect was invisible; on ext4 it split the project's external
state in two.

Every assertion here compares the *name string*, not just ``Path`` equality:
``Path("project-x") == Path("PROJECT-x")`` is true on Windows, so a name-level
assertion is the only form that fails on both families of filesystem.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ogx.cli import _external_report_dir
from ogx.config import STATE_KEY_SLUG_FALLBACK, state_key_slug


class StateKeySlugUnitTests(unittest.TestCase):
    """The documented slug rule itself."""

    def test_slug_folds_ascii_case(self):
        self.assertEqual(state_key_slug("project"), "project")
        self.assertEqual(state_key_slug("PROJECT"), "project")
        self.assertEqual(state_key_slug("My Project"), "my-project")

    def test_slug_folds_unicode_case_with_casefold(self):
        # str.lower() would also fold these two, but only casefold agrees with
        # the digest input for expansions such as the German sharp s.
        self.assertEqual(state_key_slug("straße"), state_key_slug("STRASSE"))
        self.assertEqual(state_key_slug("Ä"), state_key_slug("ä"))

    def test_slug_is_ascii_lowercase_and_portable(self):
        for name in ("PROJECT", "My Project №1", "a.b_c-d", "café"):
            with self.subTest(name=name):
                slug = state_key_slug(name)
                self.assertTrue(slug)
                self.assertRegex(slug, r"^[a-z0-9._-]+$")

    def test_slug_never_contains_a_separator_or_traversal_component(self):
        for name in ("..", "../escape", "a/b", "a\\b", "  ", "###"):
            with self.subTest(name=name):
                slug = state_key_slug(name)
                self.assertNotIn("/", slug)
                self.assertNotIn("\\", slug)
                self.assertNotIn("..", slug)
                self.assertNotIn(slug, {".", ".."})

    def test_unrepresentable_name_uses_the_documented_fallback(self):
        self.assertEqual(state_key_slug("§§§"), STATE_KEY_SLUG_FALLBACK)
        self.assertEqual(state_key_slug(""), STATE_KEY_SLUG_FALLBACK)


class StateKeyDirectoryTests(unittest.TestCase):
    """The directory name and path produced from a real location."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.base = Path(self._temp.name)
        self.state = self.base / "state-home"

    def tearDown(self):
        self._temp.cleanup()

    def _report_dir(self, project: Path) -> Path:
        with mock.patch.dict(os.environ, {"OGX_HOME": str(self.state)}):
            return _external_report_dir(project)

    def _key_dir(self, project: Path) -> Path:
        return self._report_dir(project).parent

    # --- the four failing-check reproductions -------------------------------

    def test_case_only_difference_in_the_leaf_folder_maps_to_one_state_directory(self):
        lower = self.base / "alpha" / "project"
        upper = self.base / "alpha" / "PROJECT"
        self.assertEqual(self._report_dir(lower), self._report_dir(upper))
        self.assertEqual(self._key_dir(lower).name, self._key_dir(upper).name)

    def test_case_only_difference_in_a_parent_folder_maps_to_one_state_directory(self):
        lower = self.base / "alpha" / "project"
        upper = self.base / "ALPHA" / "project"
        self.assertEqual(self._report_dir(lower), self._report_dir(upper))
        self.assertEqual(self._key_dir(lower).name, self._key_dir(upper).name)

    def test_case_only_difference_in_the_project_folder_name_maps_to_one_directory(self):
        lower = self.base / "alpha"
        upper = self.base / "ALPHA"
        self.assertEqual(self._report_dir(lower), self._report_dir(upper))
        self.assertEqual(self._key_dir(lower).name, self._key_dir(upper).name)

    # --- negative coverage: distinct projects must stay distinct ------------

    def test_different_project_names_remain_distinct(self):
        pairs = [
            (self.base / "alpha" / "project", self.base / "alpha" / "project2"),
            (self.base / "alpha" / "project", self.base / "beta" / "project"),
            (self.base / "alpha", self.base / "beta"),
        ]
        for first, second in pairs:
            with self.subTest(first=str(first), second=str(second)):
                self.assertNotEqual(self._report_dir(first), self._report_dir(second))
                self.assertNotEqual(self._key_dir(first).name, self._key_dir(second).name)

    def test_different_unicode_names_do_not_collide(self):
        # Non-ASCII characters are replaced with '-', so these two *different*
        # projects share one lossy slug. Only the digest keeps them apart.
        first = self.base / "café"
        second = self.base / "cafè"
        self.assertEqual(state_key_slug(first.name), state_key_slug(second.name))
        self.assertNotEqual(self._report_dir(first), self._report_dir(second))

    # --- separators, determinism, and the documented key shape --------------

    def test_redundant_separators_and_dot_segments_resolve_to_one_identity(self):
        canonical = Path("alpha") / "project"
        spellings = [
            Path(f"alpha{os.sep}project"),
            Path(f"alpha{os.sep}{os.sep}project"),
            Path(f"alpha{os.sep}.{os.sep}project"),
            Path(f"alpha{os.sep}project{os.sep}"),
        ]
        expected = self._report_dir(self.base / canonical)
        for spelling in spellings:
            with self.subTest(spelling=str(spelling)):
                self.assertEqual(self._report_dir(self.base / spelling), expected)

    def test_repeated_calls_are_deterministic(self):
        project = self.base / "alpha" / "PROJECT"
        first = self._report_dir(project)
        for _ in range(3):
            self.assertEqual(self._report_dir(project), first)
        self.assertEqual(self._report_dir(Path(project)), first)

    def test_directory_name_is_the_folded_slug_plus_the_sixteen_hex_digest(self):
        project = self.base / "Alpha" / "Project"
        digest = hashlib.sha256(
            str(project.resolve()).casefold().encode("utf-8")
        ).hexdigest()[:16]
        self.assertEqual(self._key_dir(project).name, f"{state_key_slug(project.name)}-{digest}")
        suffix = self._key_dir(project).name.rsplit("-", 1)[-1]
        self.assertEqual(len(suffix), 16)
        int(suffix, 16)

    def test_state_directory_stays_under_the_configured_state_root(self):
        project = self.base / "alpha" / "project"
        report_dir = self._report_dir(project)
        self.assertTrue(report_dir.resolve().is_relative_to((self.state / "projects").resolve()))
        self.assertEqual(report_dir.name, "reports")


if __name__ == "__main__":  # pragma: no cover - manual invocation
    unittest.main()
