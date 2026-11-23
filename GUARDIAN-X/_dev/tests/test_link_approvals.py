import tempfile
import unittest
from pathlib import Path

from ogx.governance.approvals import validate_operation


class LinkApprovalTests(unittest.TestCase):
    def test_integration_apply_requires_valid_link_groups(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                validate_operation("integration.apply", {"path": tmp})
            with self.assertRaises(ValueError):
                validate_operation("integration.apply", {"path": tmp, "groups": "nope"})
            result = validate_operation("integration.apply", {"path": tmp, "groups": "deps, agents"})
            self.assertEqual(result["groups"], "deps,agents")
            self.assertEqual(Path(result["path"]), Path(tmp).resolve())

    def test_integration_remove_needs_no_groups(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertNotIn("groups", validate_operation("integration.remove", {"path": tmp}))


if __name__ == "__main__":
    unittest.main()
