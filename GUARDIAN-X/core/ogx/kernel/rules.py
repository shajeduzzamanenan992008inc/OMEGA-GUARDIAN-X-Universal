"""Shared static rule catalog.

The catalog is deliberately declarative and side-effect free.  Rule IDs are
public contracts: do not renumber an existing rule when adding new ones.

All rules operate on bounded text supplied by the caller.  They never read
files, execute commands, resolve packages, or contact the network.
"""

from __future__ import annotations

import re
from typing import Final

# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------

SECRET_PATTERNS: Final = [
    ("OGX-SEC-001", "Possible hard-coded credential", re.compile(
        r"(?i)\b(api[_-]?key|access[_-]?token|client[_-]?secret|password|passwd|secret[_-]?key)\b\s*[:=]\s*(['\"])([^'\"\n]{12,})\2"
    )),
    ("OGX-SEC-002", "Private key material", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
]

PLACEHOLDER_WORDS: Final = (
    "changeme", "change_me", "example", "placeholder", "your_", "<", "test", "dummy", "xxxxx", "xxxx",
)

TEXT_SUFFIXES: Final = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".java", ".kt", ".go", ".rs",
    ".php", ".rb", ".cs", ".swift", ".c", ".h", ".cpp", ".hpp", ".json", ".toml", ".yaml",
    ".yml", ".xml", ".ini", ".cfg", ".conf", ".env", ".properties", ".sh", ".ps1", ".bat",
    ".sql", ".html", ".htm", ".css", ".scss", ".md", ".txt", ".lock", ".gradle", ".dockerfile",
    ".tf", ".dart", ".scala", ".ex", ".exs", ".lua", ".sol", ".proto", ".csproj", ".fsproj",
    ".vbproj", ".kts", ".gradle", ".graphql", ".gql", ".sass", ".less", ".m", ".mm",
}


def is_placeholder(value: str) -> bool:
    """Return True for values that are clearly documentation placeholders."""
    low = value.strip().lower()
    if low in {"changeme", "change_me", "example", "placeholder", "test", "dummy", "password", "secret", "your_api_key"}:
        return True
    if low.startswith(("${", "%", "env(", "os.getenv", "your_")):
        return True
    if re.search(r"(?:^|[^a-z0-9])(?:change[_-]?me|example|placeholder|dummy|test)(?:$|[^a-z0-9])", low):
        return True
    return bool(re.fullmatch(r"x{4,}|0{8,}", low))


def redact_evidence(value: str) -> str:
    """Redact credential-shaped values from evidence before it is stored."""
    if not value:
        return value
    text = str(value)
    # Never keep PEM payload lines, even if a caller passes surrounding text.
    text = re.sub(
        r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----.*?(?:-----END (?:[A-Z ]+ )?PRIVATE KEY-----|$)",
        "<redacted-private-key>",
        text,
        flags=re.DOTALL,
    )
    text = re.sub(
        r"(?i)\b(api[_-]?key|access[_-]?token|client[_-]?secret|password|passwd|secret[_-]?key)\b(\s*[:=]\s*)(['\"]?)([^'\"\s,;]{8,})\3",
        lambda match: f"{match.group(1)}{match.group(2)}{match.group(3)}<redacted>{match.group(3)}",
        text,
    )
    return text


# ---------------------------------------------------------------------------
# Application-code patterns
# ---------------------------------------------------------------------------

CODE_CHECKS: Final = [
    ("OGX-CODE-001", "Subprocess shell execution", re.compile(r"\bshell\s*=\s*True\b"), "medium",
     "Shell execution can create command-injection risk when inputs are not strictly controlled.",
     "Prefer argument arrays with shell=False; validate and constrain any unavoidable shell inputs."),
    ("OGX-CODE-002", "TLS certificate verification disabled", re.compile(r"\bverify\s*=\s*False\b"), "high",
     "TLS certificate verification appears to be disabled.",
     "Enable certificate verification and use a trusted CA bundle; do not suppress TLS validation in production."),
    ("OGX-CODE-003", "Dynamic code evaluation", re.compile(r"\b(?:eval|exec)\s*\("), "medium",
     "Dynamic code evaluation can execute attacker-controlled input if data flow is unsafe.",
     "Avoid eval/exec for untrusted input; use constrained parsers or explicit dispatch tables."),
    ("OGX-CODE-004", "Potential unsafe YAML loading", re.compile(r"\byaml\.load\s*\("), "medium",
     "yaml.load may construct arbitrary Python objects depending on loader and library version.",
     "Use yaml.safe_load for untrusted YAML and verify the loader argument in context."),
    ("OGX-CODE-005", "Potential unsafe deserialization", re.compile(r"\b(?:pickle\.loads?|joblib\.load|dill\.loads?)\s*\("), "high",
     "Deserialization APIs may execute code or instantiate unsafe objects when input is untrusted.",
     "Do not deserialize untrusted data with pickle-compatible formats; prefer safe, schema-validated formats."),
    ("OGX-CODE-006", "Potential weak random generator", re.compile(r"\brandom\.(?:random|randint|choice)\s*\("), "low",
     "The standard random module is not intended for security-sensitive tokens.",
     "Use secrets or an OS-backed cryptographic random source for tokens, reset links and security identifiers."),
    ("OGX-CODE-007", "Potential permissive CORS configuration", re.compile(r"(?i)(?:allow_origins|Access-Control-Allow-Origin)[^\n]{0,100}(?:\*|\[\s*['\"]\*['\"]\s*\])"), "medium",
     "A wildcard CORS origin may expose authenticated browser endpoints to untrusted origins.",
     "Use an explicit origin allowlist and review credential settings and endpoint sensitivity."),
    ("OGX-CODE-008", "Potential disabled authentication or authorization", re.compile(r"(?i)(?:auth(?:entication|orization)?|csrf|verify[_-]?token)\s*[:=]\s*(?:false|0|none)\b"), "medium",
     "A security-related control appears disabled; context is required to determine impact.",
     "Review the setting and keep authentication, authorization and CSRF checks enabled for protected operations."),
]

# ---------------------------------------------------------------------------
# Shell / PowerShell patterns (heuristic)
# ---------------------------------------------------------------------------

SHELL_CHECKS: Final = [
    ("OGX-SH-001", "Remote script piped to a shell",
     re.compile(r"(?i)\b(?:curl|wget)\b[^\n|]{0,300}\|\s*(?:sudo\s+)?(?:ba|z|k)?sh\b"), "high",
     "A downloaded script is piped directly to a shell, bypassing review and integrity checks.",
     "Download to a file, verify a published checksum/signature, review the script, then execute it explicitly."),
    ("OGX-SH-002", "Recursive forced deletion from a variable path",
     re.compile(r"(?i)\brm\s+-[a-z]*r[a-z]*f[a-z]*\s+[\"']?\$(?:\{)?[A-Za-z_]"), "medium",
     "A recursive forced delete uses an unchecked variable path and may remove an unintended directory.",
     "Validate the path is non-empty, absolute, inside an expected root, and guard the command before deletion."),
    ("OGX-SH-003", "Dynamic shell evaluation",
     re.compile(r"(?i)(?:^|[;&|]\s*)eval\s+[\"']?\$"), "medium",
     "Dynamic evaluation can execute attacker-controlled content if the variable is not strictly trusted.",
     "Avoid eval; use explicit argument arrays and validate inputs."),
]

POWERSHELL_CHECKS: Final = [
    ("OGX-PS-001", "PowerShell dynamic expression execution",
     re.compile(r"(?i)\b(?:Invoke-Expression|iex)\b"), "medium",
     "Invoke-Expression can execute attacker-controlled text if the input is not strictly trusted.",
     "Avoid Invoke-Expression; call approved commands with validated parameters."),
    ("OGX-PS-002", "PowerShell execution policy bypass",
     re.compile(r"(?i)\bSet-ExecutionPolicy\b[^\n]{0,80}\b(?:Bypass|Unrestricted)\b"), "medium",
     "Weakening the execution policy reduces a defense-in-depth control.",
     "Use signed scripts and RemoteSigned/AllSigned policy where operationally possible."),
]

# ---------------------------------------------------------------------------
# Container / infrastructure patterns
# ---------------------------------------------------------------------------

DOCKER_CHECKS: Final = [
    ("OGX-INFRA-001", "Unpinned container base image",
     re.compile(r"(?im)^\s*FROM\s+[^\s:]+:latest\b"), "medium",
     "A container build uses a :latest base image tag, which can make builds non-reproducible.",
     "Pin a reviewed immutable image tag or digest and update it through a controlled process."),
]

KUBERNETES_CHECKS: Final = [
    ("OGX-K8S-001", "Privileged container",
     re.compile(r"(?im)^\s*privileged\s*:\s*true\s*$"), "high",
     "A container requests privileged mode, which grants broad host-level capabilities.",
     "Remove privileged mode; grant only the specific Linux capabilities that are required."),
    ("OGX-K8S-002", "Host namespace sharing",
     re.compile(r"(?im)^\s*(?:hostNetwork|hostPID|hostIPC)\s*:\s*true\s*$"), "medium",
     "Sharing a host namespace weakens container isolation and may expose host resources.",
     "Disable host namespace sharing unless there is a reviewed operational requirement."),
    ("OGX-K8S-003", "Privilege escalation not disabled",
     re.compile(r"(?im)^\s*allowPrivilegeEscalation\s*:\s*true\s*$"), "medium",
     "allowPrivilegeEscalation=true permits a process to gain more privileges than its parent.",
     "Set allowPrivilegeEscalation=false and run as a non-root user where possible."),
    ("OGX-K8S-004", "Container runs as root",
     re.compile(r"(?im)^\s*runAsUser\s*:\s*0\s*$"), "medium",
     "The workload explicitly runs as UID 0 (root).",
     "Run with a dedicated non-root UID and set runAsNonRoot=true."),
    ("OGX-K8S-005", "Mutable latest image tag",
     re.compile(r"(?im)^\s*image\s*:\s*[^\s:]+:latest\s*$"), "low",
     "A workload uses a mutable :latest image tag.",
     "Use an immutable tag or digest and update it through a reviewed process."),
]

TERRAFORM_CHECKS: Final = [
    ("OGX-TF-001", "Public storage ACL",
     re.compile(r"(?i)\bacl\s*=\s*\"public-read(?:-write)?\""), "high",
     "A storage resource is configured with a public ACL.",
     "Use private ACLs and explicitly reviewed access policies; enable public-access blocks where available."),
    ("OGX-TF-002", "Storage encryption disabled",
     re.compile(r"(?i)\bencrypted\s*=\s*false\b"), "medium",
     "A resource explicitly disables encryption at rest.",
     "Enable provider-managed or customer-managed encryption and verify key policy."),
]

CI_CHECKS: Final = [
    ("OGX-CI-001", "Potential untrusted GitHub expression in run step",
     re.compile(r"(?is)\b(?:github\.event\.(?:issue|comment|review|pull_request)\.(?:title|body|comment|head\.ref))\b[\s\S]{0,400}?^\s*run\s*:.*\$\{\{"),
     "high",
     "A workflow may interpolate untrusted event data directly into a shell run command.",
     "Pass untrusted event values through environment variables and quote/validate them; avoid direct expression interpolation in shell scripts."),
]


def line_for(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1
