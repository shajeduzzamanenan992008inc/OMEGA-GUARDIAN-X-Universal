from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ogx.enterprise.approvals import ApprovalManager
from ogx.enterprise.mailer import MailReceipt, BrevoMailer, SMTPMailer, build_approval_email
from ogx.enterprise.store import EnterpriseStore


class FakeMailer:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.messages = []

    def send(self, *, recipient: str, subject: str, text_body: str, html_body: str):
        self.messages.append({"recipient": recipient, "subject": subject,
                              "text_body": text_body, "html_body": html_body})
        if self.fail:
            raise RuntimeError("simulated mail failure")
        return MailReceipt("fake", "fake-message-001")


class ApprovalWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "project"
        self.root.mkdir()
        self.store = EnterpriseStore(Path(self.tmp.name) / "state.sqlite3")
        self.mailer = FakeMailer()
        self.manager = ApprovalManager(self.store, self.mailer)
        self.env = patch.dict(os.environ, {
            "OGX_APPROVER_EMAIL": "approver@example.com",
            "OGX_APPROVER_SUBJECT": "idp|approver",
            "OGX_ACTOR_SUBJECT": "idp|requester",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.tmp.cleanup)

    def _request(self):
        result = self.manager.create(
            operation="integration.apply",
            payload={"path": str(self.root)},
            requester_subject="idp|requester",
            summary="Create missing Guardian integration files after review.",
        )
        code = re.search(r"One-time approval code: ([A-Z2-9]{12})", self.mailer.messages[-1]["text_body"])
        self.assertIsNotNone(code)
        return result, code.group(1)

    def test_email_is_sent_before_request_becomes_pending_and_code_is_not_stored(self):
        result, code = self._request()
        self.assertEqual(result["status"], "pending")
        self.assertEqual(self.mailer.messages[0]["recipient"], "approver@example.com")
        with self.store._connection() as conn:
            row = conn.execute("SELECT code_hash,status FROM approval_requests WHERE id=?", (result["request_id"],)).fetchone()
            events = conn.execute("SELECT event_type FROM approval_events WHERE request_id=? ORDER BY occurred_at", (result["request_id"],)).fetchall()
        self.assertEqual(row["status"], "pending")
        self.assertNotEqual(row["code_hash"], code)
        self.assertEqual([x["event_type"] for x in events], ["request.created", "email.sent"])

    def test_wrong_code_and_wrong_approver_are_rejected(self):
        result, code = self._request()
        with self.assertRaises(PermissionError):
            self.manager.decide(request_id=result["request_id"], code="A" * 12,
                                approver_subject="idp|approver", decision="approved")
        with self.assertRaises(PermissionError):
            self.manager.decide(request_id=result["request_id"], code=code,
                                approver_subject="idp|intruder", decision="approved")
        self.assertEqual(self.manager.list_requests()[0]["status"], "pending")

    def test_requester_cannot_be_approver(self):
        with patch.dict(os.environ, {"OGX_APPROVER_SUBJECT": "idp|requester"}):
            with self.assertRaises(PermissionError):
                self.manager.create(operation="integration.apply", payload={"path": str(self.root)},
                                    requester_subject="idp|requester", summary="Must enforce separation.")
        self.assertEqual(self.mailer.messages, [])

    def test_approved_action_is_single_use_and_requester_bound(self):
        result, code = self._request()
        self.manager.decide(request_id=result["request_id"], code=code,
                            approver_subject="idp|approver", decision="approved")
        with self.assertRaises(PermissionError):
            self.manager.claim_execution(request_id=result["request_id"], requester_subject="idp|other")
        action = self.manager.claim_execution(request_id=result["request_id"], requester_subject="idp|requester")
        self.assertEqual(action["operation"], "integration.apply")
        self.manager.finish_execution(result["request_id"], success=True, result_summary="created 2 files")
        with self.assertRaises(PermissionError):
            self.manager.claim_execution(request_id=result["request_id"], requester_subject="idp|requester")
        self.assertEqual(self.manager.list_requests()[0]["status"], "executed")

    def test_delivery_failure_cannot_be_approved(self):
        manager = ApprovalManager(self.store, FakeMailer(fail=True))
        with self.assertRaises(RuntimeError):
            manager.create(operation="integration.apply", payload={"path": str(self.root)},
                           requester_subject="idp|requester", summary="Mail provider unavailable.")
        self.assertEqual(manager.list_requests()[0]["status"], "delivery_failed")

    def test_invalid_code_attempts_lock_request_after_five_tries(self):
        result, correct_code = self._request()
        for index in range(5):
            with self.assertRaises(PermissionError):
                self.manager.decide(request_id=result["request_id"], code=f"Z{index:011d}",
                                    approver_subject="idp|approver", decision="approved")
        with self.assertRaises(PermissionError):
            self.manager.decide(request_id=result["request_id"], code=correct_code,
                                approver_subject="idp|approver", decision="approved")
        with self.store._connection() as conn:
            row = conn.execute("SELECT failed_attempts,locked_at FROM approval_requests WHERE id=?",
                               (result["request_id"],)).fetchone()
        self.assertEqual(row["failed_attempts"], 5)
        self.assertIsNotNone(row["locked_at"])

    def test_clone_and_pull_request_actions_are_narrowly_validated(self):
        destination = self.root.parent / "clone-target"
        clone = self.manager.create(operation="repo.clone",
            payload={"url": "https://github.com/acme/app.git", "destination": str(destination)},
            requester_subject="idp|requester", summary="Clone a repository to an empty destination.")
        self.assertEqual(clone["status"], "pending")
        self.assertIn("https://github.com/acme/app.git", self.mailer.messages[-1]["text_body"])
        with self.assertRaises(ValueError):
            self.manager.create(operation="repo.clone",
                payload={"url": "https://user:password@github.com/acme/app.git", "destination": str(destination)},
                requester_subject="idp|requester", summary="Credentials in URL must be rejected.")
        pr = self.manager.create(operation="platform.pr", payload={
            "provider": "github", "url": "https://github.com/acme/app", "title": "Fix issue",
            "body": "Please review.", "head": "fix/issue-123", "base": "main",
        }, requester_subject="idp|requester", summary="Create a reviewed pull request.")
        self.assertEqual(pr["status"], "pending")
        with self.assertRaises(ValueError):
            self.manager.create(operation="platform.pr", payload={
                "provider": "github", "url": "javascript://github.com/acme/app", "title": "x",
                "head": "fix", "base": "main",
            }, requester_subject="idp|requester", summary="Reject non-HTTP URLs.")

    def test_approved_target_path_cannot_be_swapped_to_symlink(self):
        result, code = self._request()
        self.manager.decide(request_id=result["request_id"], code=code,
                            approver_subject="idp|approver", decision="approved")
        moved = self.root.parent / "project-original"
        self.root.rename(moved)
        try:
            self.root.symlink_to(moved, target_is_directory=True)
        except OSError:
            self.skipTest("Directory symlinks unavailable on this platform.")
        with self.assertRaises(PermissionError):
            self.manager.claim_execution(request_id=result["request_id"], requester_subject="idp|requester")

    def test_guardian_repair_plan_must_match_the_emailed_plan(self):
        (self.root / ".env").write_text("TOKEN=local-value\n", encoding="utf-8")
        request = self.manager.create(operation="guardian.repair", payload={"path": str(self.root)},
            requester_subject="idp|requester", summary="Add missing env ignore patterns.")
        code = re.search(r"One-time approval code: ([A-Z2-9]{12})", self.mailer.messages[-1]["text_body"]).group(1)
        self.manager.decide(request_id=request["request_id"], code=code,
            approver_subject="idp|approver", decision="approved")
        (self.root / ".gitignore").write_text(".env\n.env.*\n", encoding="utf-8")
        with self.assertRaises(PermissionError):
            self.manager.claim_execution(request_id=request["request_id"], requester_subject="idp|requester")

    def test_branch_create_approval_is_bound_to_the_same_git_head(self):
        import shutil
        import subprocess
        if not shutil.which("git"):
            self.skipTest("Git executable unavailable.")
        def git(*args):
            subprocess.run(["git", *args], cwd=self.root, check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        git("init")
        git("config", "user.email", "ogx-test@example.com")
        git("config", "user.name", "OGX Test")
        (self.root / "tracked.txt").write_text("first\n", encoding="utf-8")
        git("add", "tracked.txt")
        git("commit", "-m", "initial")
        request = self.manager.create(operation="repo.branch_create",
            payload={"path": str(self.root), "name": "feature/approved"},
            requester_subject="idp|requester", summary="Create feature branch from reviewed HEAD.")
        code = re.search(r"One-time approval code: ([A-Z2-9]{12})", self.mailer.messages[-1]["text_body"]).group(1)
        self.manager.decide(request_id=request["request_id"], code=code,
            approver_subject="idp|approver", decision="approved")
        (self.root / "tracked.txt").write_text("second\n", encoding="utf-8")
        git("add", "tracked.txt")
        git("commit", "-m", "changed after request")
        with self.assertRaises(PermissionError):
            self.manager.claim_execution(request_id=request["request_id"], requester_subject="idp|requester")

    def test_push_approval_is_bound_to_the_same_feature_commit(self):
        import shutil
        import subprocess
        if not shutil.which("git"):
            self.skipTest("Git executable unavailable.")
        def git(*args):
            subprocess.run(["git", *args], cwd=self.root, check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        git("init")
        git("config", "user.email", "ogx-test@example.com")
        git("config", "user.name", "OGX Test")
        (self.root / "tracked.txt").write_text("first\n", encoding="utf-8")
        git("add", "tracked.txt")
        git("commit", "-m", "initial")
        git("switch", "-c", "feature/reviewed")
        request = self.manager.create(operation="repo.push",
            payload={"path": str(self.root), "branch": "feature/reviewed", "remote": "origin"},
            requester_subject="idp|requester", summary="Push reviewed feature commit.")
        code = re.search(r"One-time approval code: ([A-Z2-9]{12})", self.mailer.messages[-1]["text_body"]).group(1)
        self.manager.decide(request_id=request["request_id"], code=code,
            approver_subject="idp|approver", decision="approved")
        (self.root / "tracked.txt").write_text("modified after email\n", encoding="utf-8")
        git("add", "tracked.txt")
        git("commit", "-m", "changed after request")
        with self.assertRaises(PermissionError):
            self.manager.claim_execution(request_id=request["request_id"], requester_subject="idp|requester")

    def test_unsupported_operation_and_untrusted_remote_are_rejected(self):
        with self.assertRaises(ValueError):
            self.manager.create(operation="shell.exec", payload={"path": str(self.root)},
                                requester_subject="idp|requester", summary="Never shell.")
        with self.assertRaises(ValueError):
            self.manager.create(operation="guardian.upgrade_fetch",
                                payload={"path": str(self.root), "remote": "origin; rm -rf /"},
                                requester_subject="idp|requester", summary="Invalid remote.")
        self.assertEqual(self.mailer.messages, [])

    def test_approval_email_escapes_untrusted_html_fields(self):
        _, _, html = build_approval_email(request_id="id", operation="x",
            project="<script>alert(1)</script>", summary="<img src=x>", code="ABCD2345EFGH",
            expires_at="tomorrow")
        self.assertNotIn("<script>", html)
        self.assertNotIn("<img src=x>", html)


class BrevoMailerTests(unittest.TestCase):
    def test_brevo_api_request_uses_transactional_endpoint_and_api_key_header(self):
        import urllib.request
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, size=-1): return b'{"messageId":"mail-123"}'
        with patch("urllib.request.urlopen", return_value=Response()) as urlopen:
            mailer = BrevoMailer(api_key="unit-test-key", sender_email="sender@example.com")
            receipt = mailer.send(recipient="approver@example.com", subject="Approve", text_body="Code", html_body="<p>Code</p>")
        self.assertEqual(receipt.provider, "brevo")
        self.assertEqual(receipt.message_id, "mail-123")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.brevo.com/v3/smtp/email")
        self.assertEqual(request.get_header("Api-key"), "unit-test-key")
        payload = json.loads(request.data)
        self.assertEqual(payload["to"][0]["email"], "approver@example.com")


class SMTPMailerTests(unittest.TestCase):
    @patch("ogx.enterprise.mailer.smtplib.SMTP")
    @patch("ogx.enterprise.mailer.ssl.create_default_context")
    def test_smtp_587_uses_verified_starttls(self, create_context, smtp_cls):
        client = smtp_cls.return_value.__enter__.return_value
        client.send_message.return_value = {}
        mailer = SMTPMailer(host="mail.example.com", port=587, username="user",
                            password="secret", sender_email="sender@example.com")
        receipt = mailer.send(recipient="approver@example.com", subject="Approval",
                              text_body="Code: ABC", html_body="<p>Code</p>")
        self.assertEqual(receipt.provider, "smtp")
        smtp_cls.assert_called_once_with("mail.example.com", 587, timeout=20)
        client.starttls.assert_called_once_with(context=create_context.return_value)
        client.login.assert_called_once_with("user", "secret")
        client.send_message.assert_called_once()

    @patch("ogx.enterprise.mailer.smtplib.SMTP_SSL")
    @patch("ogx.enterprise.mailer.ssl.create_default_context")
    def test_smtp_465_uses_implicit_tls(self, create_context, smtp_ssl_cls):
        client = smtp_ssl_cls.return_value.__enter__.return_value
        client.send_message.return_value = {}
        mailer = SMTPMailer(host="mail.example.com", port=465, username="user",
                            password="secret", sender_email="sender@example.com")
        receipt = mailer.send(recipient="approver@example.com", subject="Approval",
                              text_body="Code: ABC", html_body="<p>Code</p>")
        self.assertEqual(receipt.provider, "smtp")
        smtp_ssl_cls.assert_called_once_with("mail.example.com", 465, timeout=20,
                                             context=create_context.return_value)
        client.starttls.assert_not_called()
        client.login.assert_called_once_with("user", "secret")
        client.send_message.assert_called_once()


class EnterpriseGovernanceStoreTests(unittest.TestCase):
    def test_six_pillar_framework_and_governance_record_permissions(self):
        from ogx.enterprise.governance import framework_document
        framework = framework_document()
        self.assertEqual({x["id"] for x in framework["pillars"]},
                         {"leadership", "direction", "management", "administration", "steering", "operations"})
        with tempfile.TemporaryDirectory() as tmp:
            store = EnterpriseStore(Path(tmp) / "enterprise.sqlite3")
            store.initialize()
            org = store.create_organization("northstar", "Northstar", "owner")
            store.add_member(org["organization_id"], "developer", "developer", "owner")
            store.add_member(org["organization_id"], "auditor", "auditor", "owner")
            record = store.create_governance_record(org["organization_id"], "incident",
                "API latency degradation", "developer", "high", "open", "developer",
                details={"service": "api", "impact": "elevated latency"})
            self.assertEqual(record["record_type"], "incident")
            with self.assertRaises(PermissionError):
                store.create_governance_record(org["organization_id"], "objective",
                    "Strategic goal", "owner", "high", "planned", "developer")
            objective = store.create_governance_record(org["organization_id"], "objective",
                "Improve delivery reliability", "owner", "high", "planned", "owner")
            self.assertEqual(objective["status"], "planned")
            rows = store.list_governance_records(org["organization_id"], "auditor")
            self.assertEqual(len(rows), 2)
            overview = store.governance_overview(org["organization_id"], "auditor")
            self.assertEqual(overview["members"], 3)
            with self.assertRaises(PermissionError):
                store.governance_overview(org["organization_id"], "developer")


if __name__ == "__main__":
    unittest.main()
