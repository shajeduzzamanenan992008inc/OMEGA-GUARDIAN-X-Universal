from __future__ import annotations

import ast
import os
import re
import stat
from pathlib import Path
from typing import Mapping

from .config import state_dir

ENV_TEMPLATE = """# OMEGA GUARDIAN X — private runtime configuration
# This file is intended for the OGX external state directory, NOT your Git repository.
# Keep real credentials here or in an OS secret manager. Never commit a real .env file.
# Values already set in the operating-system environment take precedence.
# This file uses simple KEY=VALUE syntax; shell commands and variable expansion are not executed.

# -----------------------------------------------------------------------------
# Approval email — choose 'brevo' or 'smtp'
# -----------------------------------------------------------------------------
OGX_MAIL_PROVIDER=brevo
OGX_BREVO_API_KEY=
OGX_MAIL_FROM_EMAIL=
OGX_MAIL_FROM_NAME=OMEGA GUARDIAN X

# Designated approver and the external identity subjects used by your identity provider
OGX_APPROVER_EMAIL=
OGX_APPROVER_SUBJECT=
OGX_ACTOR_SUBJECT=
OGX_APPROVAL_TTL_MINUTES=30

# For Bravo/another branded mail provider, select smtp only if it supplies outbound SMTP credentials.
# SMTP-compatible provider settings (used only when OGX_MAIL_PROVIDER=smtp)
OGX_SMTP_HOST=
OGX_SMTP_PORT=587
OGX_SMTP_USERNAME=
OGX_SMTP_PASSWORD=

# -----------------------------------------------------------------------------
# Git hosting API tokens — optional; use least-privilege scopes
# -----------------------------------------------------------------------------
OGX_GITHUB_TOKEN=
OGX_GITHUB_API_BASE=https://api.github.com
OGX_GITLAB_TOKEN=
OGX_GITLAB_API_BASE=https://gitlab.com/api/v4
OGX_BITBUCKET_TOKEN=
OGX_GITEA_TOKEN=
OGX_FORGEJO_TOKEN=

# -----------------------------------------------------------------------------
# Safety controls
# -----------------------------------------------------------------------------
# Keep disabled. Setting this to 1 bypasses email approval for confirmed writes.
OGX_ALLOW_UNAPPROVED_WRITES=0

# Bootstrap settings are intentionally controlled by the OS environment, not this file:
# OGX_HOME       — alternate external OGX state directory
# OGX_ENV_FILE   — explicit path to an alternate env file
"""

SECRET_KEYS = {
    "OGX_BREVO_API_KEY", "OGX_SMTP_PASSWORD", "OGX_GITHUB_TOKEN",
    "OGX_GITLAB_TOKEN", "OGX_BITBUCKET_TOKEN", "OGX_GITEA_TOKEN", "OGX_FORGEJO_TOKEN",
}
KNOWN_ENV_KEYS = {
    "OGX_MAIL_PROVIDER", "OGX_BREVO_API_KEY", "OGX_MAIL_FROM_EMAIL", "OGX_MAIL_FROM_NAME",
    "OGX_APPROVER_EMAIL", "OGX_APPROVER_SUBJECT", "OGX_ACTOR_SUBJECT",
    "OGX_APPROVAL_TTL_MINUTES", "OGX_SMTP_HOST", "OGX_SMTP_PORT", "OGX_SMTP_USERNAME",
    "OGX_SMTP_PASSWORD", "OGX_GITHUB_TOKEN", "OGX_GITHUB_API_BASE", "OGX_GITLAB_TOKEN",
    "OGX_GITLAB_API_BASE", "OGX_BITBUCKET_TOKEN", "OGX_GITEA_TOKEN", "OGX_FORGEJO_TOKEN",
    "OGX_ALLOW_UNAPPROVED_WRITES",
}


class EnvFileError(ValueError):
    """Raised when an OGX env file is unsafe or malformed."""


def resolve_env_path() -> Path:
    """Resolve explicit OGX_ENV_FILE or the default external-state .env file.

    Never searches the current directory or a scanned project automatically: doing so
    could let an untrusted repository change OGX's runtime credentials or safety policy.
    """
    override = os.environ.get("OGX_ENV_FILE", "").strip()
    if override:
        return Path(override).expanduser().absolute()
    return state_dir() / ".env"


def _safe_file(path: Path) -> None:
    if path.is_symlink():
        raise EnvFileError(f"OGX environment file must not be a symlink: {path}")
    if not path.exists():
        return
    try:
        info = path.stat()
    except OSError as exc:
        raise EnvFileError(f"Cannot inspect OGX environment file {path}: {exc}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise EnvFileError(f"OGX environment path must be a regular file: {path}")
    if info.st_size > 128 * 1024:
        raise EnvFileError("OGX environment file is larger than the 128 KB safety limit.")


def _parse_value(raw: str, *, line_number: int) -> str:
    value = raw.strip()
    if not value:
        return ""
    if value[0] in {"'", '"'}:
        try:
            parsed = ast.literal_eval(value)
        except (SyntaxError, ValueError) as exc:
            raise EnvFileError(f"Invalid quoted value on .env line {line_number}.") from exc
        if not isinstance(parsed, str):
            raise EnvFileError(f"Only quoted strings are allowed on .env line {line_number}.")
        return parsed
    # Support dotenv-style trailing comments without treating a # embedded in a token as a comment.
    return re.split(r"\s+#", value, maxsplit=1)[0].rstrip()


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse a small, non-executable dotenv subset and reject unknown OGX names."""
    path = path.expanduser().absolute()
    _safe_file(path)
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise EnvFileError(f"Cannot read OGX environment file {path}: {exc}") from exc

    values: dict[str, str] = {}
    for number, original in enumerate(text.splitlines(), start=1):
        line = original.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise EnvFileError(f"Expected KEY=VALUE on .env line {number}.")
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise EnvFileError(f"Invalid environment variable name on .env line {number}.")
        if key not in KNOWN_ENV_KEYS:
            raise EnvFileError(f"Unknown OGX setting {key!r} on .env line {number}; check spelling or OGX version.")
        if key in values:
            raise EnvFileError(f"Duplicate environment variable {key!r} on .env line {number}.")
        values[key] = _parse_value(raw_value, line_number=number)
    return values


def load_runtime_env() -> dict[str, str]:
    """Load the selected private env file without overriding OS-provided variables."""
    path = resolve_env_path()
    values = parse_env_file(path)
    for key, value in values.items():
        if key not in os.environ:
            os.environ[key] = value
    return values


def create_env_file(path: Path | None = None) -> Path:
    """Create a private template; never overwrite an existing env file."""
    target = (path or resolve_env_path()).expanduser().absolute()
    if target.is_symlink():
        raise EnvFileError(f"Refusing to write a symlinked environment file: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(target, flags, 0o600)
    except FileExistsError as exc:
        raise EnvFileError(f"Environment file already exists; it was not overwritten: {target}") from exc
    except OSError as exc:
        raise EnvFileError(f"Could not create environment file {target}: {exc}") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(ENV_TEMPLATE)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        try:
            target.unlink(missing_ok=True)
        finally:
            raise
    if os.name != "nt":
        os.chmod(target, 0o600)
    return target


def file_permission_warning(path: Path | None = None) -> str | None:
    target = path or resolve_env_path()
    if os.name == "nt" or not target.exists():
        return None
    _safe_file(target)
    mode = stat.S_IMODE(target.stat().st_mode)
    if mode & 0o077:
        return f"{target} permissions are {mode:04o}; restrict access (recommended: chmod 600)."
    return None


def validate_runtime_config(env: Mapping[str, str] | None = None) -> list[str]:
    """Return actionable config errors without revealing credential values."""
    values = os.environ if env is None else env
    errors: list[str] = []

    def configured(name: str) -> bool:
        value = str(values.get(name, "")).strip()
        return bool(value) and not (value.startswith("<") and value.endswith(">")) and value.lower() not in {"change-me", "changeme", "your-token", "your_api_key"}

    provider = str(values.get("OGX_MAIL_PROVIDER", "brevo")).strip().lower()
    if provider not in {"brevo", "smtp"}:
        errors.append("OGX_MAIL_PROVIDER must be 'brevo' or 'smtp'.")
    required_mail = ["OGX_MAIL_FROM_EMAIL", "OGX_APPROVER_EMAIL", "OGX_APPROVER_SUBJECT", "OGX_ACTOR_SUBJECT"]
    for name in required_mail:
        if not configured(name):
            errors.append(f"{name} is not configured.")

    from_email = str(values.get("OGX_MAIL_FROM_EMAIL", "")).strip()
    approver_email = str(values.get("OGX_APPROVER_EMAIL", "")).strip()
    email_pattern = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    for name, value in (("OGX_MAIL_FROM_EMAIL", from_email), ("OGX_APPROVER_EMAIL", approver_email)):
        if value and not email_pattern.fullmatch(value):
            errors.append(f"{name} must be a valid email address.")

    if provider == "brevo" and not configured("OGX_BREVO_API_KEY"):
        errors.append("OGX_BREVO_API_KEY is required when OGX_MAIL_PROVIDER=brevo.")
    if provider == "smtp":
        for name in ("OGX_SMTP_HOST", "OGX_SMTP_USERNAME", "OGX_SMTP_PASSWORD"):
            if not configured(name):
                errors.append(f"{name} is required when OGX_MAIL_PROVIDER=smtp.")
        try:
            port = int(str(values.get("OGX_SMTP_PORT", "587")))
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            errors.append("OGX_SMTP_PORT must be an integer from 1 to 65535.")

    try:
        ttl = int(str(values.get("OGX_APPROVAL_TTL_MINUTES", "30")))
        if not 5 <= ttl <= 1440:
            raise ValueError
    except ValueError:
        errors.append("OGX_APPROVAL_TTL_MINUTES must be an integer from 5 to 1440.")

    bypass = str(values.get("OGX_ALLOW_UNAPPROVED_WRITES", "0")).strip().lower()
    if bypass in {"1", "true", "yes", "on"}:
        errors.append("SECURITY WARNING: OGX_ALLOW_UNAPPROVED_WRITES bypasses email approval; disable it outside isolated tests.")
    elif bypass not in {"", "0", "false", "no", "off"}:
        errors.append("OGX_ALLOW_UNAPPROVED_WRITES must be 0 or 1.")
    return errors


def configured_settings(env: Mapping[str, str] | None = None) -> list[tuple[str, bool]]:
    """Return known setting names and whether they are non-empty, never their values."""
    values = os.environ if env is None else env
    return [(key, bool(str(values.get(key, "")).strip())) for key in sorted(KNOWN_ENV_KEYS)]
