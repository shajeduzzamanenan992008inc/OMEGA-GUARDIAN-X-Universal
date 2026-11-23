import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ogx.kernel.envfile import (
    EnvFileError, create_env_file, file_permission_warning, load_runtime_env,
    parse_env_file, resolve_env_path, validate_runtime_config,
)


class EnvFileTests(unittest.TestCase):
    def test_checked_in_example_covers_every_runtime_setting_and_has_no_secrets(self):
        from ogx.kernel.envfile import KNOWN_ENV_KEYS, SECRET_KEYS
        example = Path(__file__).resolve().parents[2] / ".env.example"
        values = parse_env_file(example)
        self.assertEqual(set(values), KNOWN_ENV_KEYS)
        self.assertTrue(all(values[key] == "" for key in SECRET_KEYS))

    def test_template_is_created_outside_state_and_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"OGX_HOME": str(Path(tmp) / "state")}, clear=True):
            target = create_env_file()
            self.assertEqual(target, Path(tmp) / "state" / ".env")
            self.assertIn("OGX_BREVO_API_KEY=", target.read_text(encoding="utf-8"))
            with self.assertRaises(EnvFileError):
                create_env_file()
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)

    def test_parser_supports_comments_quotes_and_export_without_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                "# comment\nexport OGX_MAIL_PROVIDER=brevo\n"
                "OGX_MAIL_FROM_NAME=Guardian # label\n"
                "OGX_APPROVER_SUBJECT='idp|approver'\n",
                encoding="utf-8",
            )
            self.assertEqual(parse_env_file(path), {
                "OGX_MAIL_PROVIDER": "brevo",
                "OGX_MAIL_FROM_NAME": "Guardian",
                "OGX_APPROVER_SUBJECT": "idp|approver",
            })

    def test_rejects_unknown_duplicate_and_malformed_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            for content in (
                "PATH=/tmp\n",
                "OGX_MAIL_PROVIDER=brevo\nOGX_MAIL_PROVIDER=smtp\n",
                "not-a-key\n",
                "OGX_APPROVER_SUBJECT='unterminated\n",
            ):
                path.write_text(content, encoding="utf-8")
                with self.assertRaises(EnvFileError):
                    parse_env_file(path)

    def test_rejects_symlinked_env_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "real.env"
            source.write_text("OGX_MAIL_PROVIDER=brevo\n", encoding="utf-8")
            link = root / ".env"
            try:
                link.symlink_to(source)
            except OSError:
                self.skipTest("Symlink creation unavailable on this platform.")
            with self.assertRaises(EnvFileError):
                parse_env_file(link)

    def test_loads_only_selected_external_file_and_os_environment_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("OGX_MAIL_PROVIDER=smtp\nOGX_SMTP_PORT=465\n", encoding="utf-8")
            with patch.dict(os.environ, {"OGX_ENV_FILE": str(path), "OGX_MAIL_PROVIDER": "brevo"}, clear=True):
                load_runtime_env()
                self.assertEqual(os.environ["OGX_MAIL_PROVIDER"], "brevo")
                self.assertEqual(os.environ["OGX_SMTP_PORT"], "465")
                self.assertEqual(resolve_env_path(), path)

    def test_does_not_implicitly_read_current_directory_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text("OGX_MAIL_PROVIDER=smtp\n", encoding="utf-8")
            state = root / "state"
            with patch.dict(os.environ, {"OGX_HOME": str(state)}, clear=True):
                self.assertEqual(load_runtime_env(), {})
                self.assertNotIn("OGX_MAIL_PROVIDER", os.environ)

    def test_validate_config_does_not_reveal_secret_values(self):
        values = {
            "OGX_MAIL_PROVIDER": "brevo", "OGX_BREVO_API_KEY": "super-secret",
            "OGX_MAIL_FROM_EMAIL": "sender@example.com", "OGX_APPROVER_EMAIL": "approver@example.com",
            "OGX_APPROVER_SUBJECT": "idp|approver", "OGX_ACTOR_SUBJECT": "idp|requester",
            "OGX_APPROVAL_TTL_MINUTES": "30", "OGX_ALLOW_UNAPPROVED_WRITES": "0",
        }
        self.assertEqual(validate_runtime_config(values), [])

    def test_invalid_provider_and_break_glass_bypass_are_reported(self):
        values = {"OGX_MAIL_PROVIDER": "unknown", "OGX_ALLOW_UNAPPROVED_WRITES": "1"}
        errors = validate_runtime_config(values)
        self.assertTrue(any("OGX_MAIL_PROVIDER" in item for item in errors))
        self.assertTrue(any("SECURITY WARNING" in item for item in errors))

    def test_permission_warning_detects_group_or_world_access(self):
        if os.name == "nt":
            self.skipTest("POSIX file mode checks are not available on Windows.")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("OGX_MAIL_PROVIDER=brevo\n", encoding="utf-8")
            path.chmod(0o644)
            self.assertIn("chmod 600", file_permission_warning(path))


if __name__ == "__main__":
    unittest.main()
