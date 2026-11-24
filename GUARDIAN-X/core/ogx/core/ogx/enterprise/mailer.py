from __future__ import annotations

import json
import os
import smtplib
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from email.message import EmailMessage
from html import escape
from typing import Protocol


@dataclass(frozen=True, slots=True)
class MailReceipt:
    provider: str
    message_id: str


class Mailer(Protocol):
    def send(self, *, recipient: str, subject: str, text_body: str, html_body: str) -> MailReceipt: ...


class MailConfigurationError(RuntimeError):
    pass


class MailDeliveryError(RuntimeError):
    pass


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise MailConfigurationError(f"Missing required mail setting: {name}")
    return value


class BrevoMailer:
    """Brevo transactional email adapter using its official HTTPS API."""

    endpoint = "https://api.brevo.com/v3/smtp/email"

    def __init__(self, api_key: str | None = None, sender_email: str | None = None,
                 sender_name: str | None = None, timeout: int = 20):
        self.api_key = api_key or _required("OGX_BREVO_API_KEY")
        self.sender_email = sender_email or _required("OGX_MAIL_FROM_EMAIL")
        self.sender_name = sender_name or os.environ.get("OGX_MAIL_FROM_NAME", "OMEGA GUARDIAN X")
        self.timeout = timeout

    def send(self, *, recipient: str, subject: str, text_body: str, html_body: str) -> MailReceipt:
        payload = {
            "sender": {"name": self.sender_name, "email": self.sender_email},
            "to": [{"email": recipient}],
            "subject": subject,
            "textContent": text_body,
            "htmlContent": html_body,
            "tags": ["ogx-governance-approval"],
        }
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"accept": "application/json", "api-key": self.api_key,
                     "content-type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read(1024 * 1024).decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # Deliberately do not include request headers or API credentials in errors.
            detail = exc.read(4096).decode("utf-8", errors="replace")
            raise MailDeliveryError(f"Brevo API returned HTTP {exc.code}: {detail[:500]}") from exc
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise MailDeliveryError(f"Brevo email delivery failed: {type(exc).__name__}") from exc
        message_id = data.get("messageId") if isinstance(data, dict) else None
        if not isinstance(message_id, str) or not message_id:
            raise MailDeliveryError("Brevo accepted no message identifier; approval email status is unknown.")
        return MailReceipt("brevo", message_id)


class SMTPMailer:
    """Generic TLS SMTP adapter for standard SMTP-compatible mail providers and private relays."""

    def __init__(self, host: str | None = None, port: int | None = None,
                 username: str | None = None, password: str | None = None,
                 sender_email: str | None = None, sender_name: str | None = None,
                 timeout: int = 20):
        self.host = host or _required("OGX_SMTP_HOST")
        self.port = port or int(os.environ.get("OGX_SMTP_PORT", "587"))
        self.username = username or _required("OGX_SMTP_USERNAME")
        self.password = password or _required("OGX_SMTP_PASSWORD")
        self.sender_email = sender_email or _required("OGX_MAIL_FROM_EMAIL")
        self.sender_name = sender_name or os.environ.get("OGX_MAIL_FROM_NAME", "OMEGA GUARDIAN X")
        self.timeout = timeout

    def send(self, *, recipient: str, subject: str, text_body: str, html_body: str) -> MailReceipt:
        message = EmailMessage()
        message["From"] = f"{self.sender_name} <{self.sender_email}>"
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(text_body)
        message.add_alternative(html_body, subtype="html")
        context = ssl.create_default_context()
        try:
            if self.port == 465:
                with smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout, context=context) as client:
                    client.login(self.username, self.password)
                    refused = client.send_message(message)
            else:
                with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as client:
                    client.ehlo()
                    client.starttls(context=context)
                    client.ehlo()
                    client.login(self.username, self.password)
                    refused = client.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            raise MailDeliveryError(f"SMTP email delivery failed: {type(exc).__name__}") from exc
        if refused:
            raise MailDeliveryError("SMTP server refused one or more recipients.")
        return MailReceipt("smtp", message.get("Message-ID", "smtp-accepted"))


def build_mailer() -> Mailer:
    provider = os.environ.get("OGX_MAIL_PROVIDER", "brevo").strip().lower()
    if provider == "brevo":
        return BrevoMailer()
    if provider == "smtp":
        return SMTPMailer()
    raise MailConfigurationError("OGX_MAIL_PROVIDER must be 'brevo' or 'smtp'.")


def build_approval_email(*, request_id: str, operation: str, project: str,
                         summary: str, code: str, expires_at: str,
                         details: dict | None = None) -> tuple[str, str, str]:
    subject = f"Approval required: {operation} — {project}"
    details_text = json.dumps(details or {}, indent=2, ensure_ascii=False)
    text_body = (
        "OMEGA GUARDIAN X approval request\n\n"
        f"Request ID: {request_id}\nOperation: {operation}\nProject/target: {project}\n"
        f"Summary: {summary}\nDetails: {details_text}\nExpires (UTC): {expires_at}\n\n"
        f"One-time approval code: {code}\n\n"
        "Review the request in your trusted OGX terminal and enter this code with the approval command.\n"
        "Do not forward this message. This code is single-use and expires automatically.\n"
        "No project change has been executed yet.\n"
    )
    html_body = (
        "<html><body><h2>OMEGA GUARDIAN X — Approval required</h2>"
        f"<p><b>Request:</b> {escape(request_id)}<br>"
        f"<b>Operation:</b> {escape(operation)}<br>"
        f"<b>Project/target:</b> {escape(project)}<br>"
        f"<b>Summary:</b> {escape(summary)}<br>"
        f"<b>Expires (UTC):</b> {escape(expires_at)}</p>"
        f"<pre>{escape(details_text)}</pre>"
        f"<p style='font-size:24px'><b>{escape(code)}</b></p>"
        "<p>Enter this single-use code in your trusted OGX terminal. Do not forward it. "
        "No project change has been executed yet.</p></body></html>"
    )
    return subject, text_body, html_body
