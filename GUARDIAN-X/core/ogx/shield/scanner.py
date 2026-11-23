from __future__ import annotations

import ast
import json
import re
import tomllib
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from ogx import __version__
from ogx.kernel.config import ScanConfig, load_config
from ogx.analysis.detector import detect_project
from ogx.kernel.models import Finding, ScanReport, relative_path, utc_now
from ogx.kernel.rules import (
    CODE_CHECKS,
    PLACEHOLDER_WORDS,
    SECRET_PATTERNS,
    TEXT_SUFFIXES,
    is_placeholder as _is_placeholder,
    line_for as _line_for,
)


def _iter_files(root: Path, config: ScanConfig) -> tuple[list[Path], int, list[str]]:
    files: list[Path] = []
    skipped = 0
    warnings: list[str] = []
    excludes = set(config.excludes)
    for current, dirs, names in __import__("os").walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        kept_dirs = []
        for directory in dirs:
            candidate = current_path / directory
            if directory in excludes or candidate.is_symlink():
                skipped += 1
            else:
                kept_dirs.append(directory)
        dirs[:] = kept_dirs
        for name in names:
            candidate = current_path / name
            try:
                if candidate.is_symlink() or not candidate.is_file():
                    skipped += 1
                    continue
                if candidate.stat().st_size > config.max_file_bytes:
                    skipped += 1
                    continue
            except OSError:
                skipped += 1
                continue
            files.append(candidate)
            if len(files) >= config.max_files:
                warnings.append(f"File limit reached ({config.max_files}); scan is partial.")
                return files, skipped, warnings
    return files, skipped, warnings


def _scan_secrets(path: Path, root: Path, text: str) -> list[Finding]:
    findings: list[Finding] = []
    rel = relative_path(path, root)
    for rule_id, title, pattern in SECRET_PATTERNS:
        for match in pattern.finditer(text):
            if rule_id == "OGX-SEC-001":
                value = match.group(3)
                if _is_placeholder(value):
                    continue
                # Do not copy the matched secret into evidence or reports.
                evidence = f"{match.group(1)}=<redacted>"
            else:
                evidence = "Private-key PEM header detected; key contents were not captured."
            findings.append(Finding(
                rule_id=rule_id, title=title, severity="high", category="secrets",
                message="A source file may contain a hard-coded credential. Verify whether it is real and rotate it if exposed.",
                path=rel, line=_line_for(text, match.start()), evidence=evidence,
                recommendation="Move credentials to an approved secret manager or environment injection; remove the exposed value and rotate it.",
                confidence="medium" if rule_id == "OGX-SEC-001" else "high",
            ))
    return findings


def _scan_code_patterns(path: Path, root: Path, text: str) -> list[Finding]:
    rel = relative_path(path, root)
    findings: list[Finding] = []
    for rule_id, title, pattern, severity, message, recommendation in CODE_CHECKS:
        for match in pattern.finditer(text):
            findings.append(Finding(rule_id, title, severity, "secure-coding", message, rel,
                                    _line_for(text, match.start()), match.group(0), recommendation, "low"))
    return findings


def _parse_requirements(path: Path, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    seen: dict[str, int] = {}
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError):
        return findings
    for number, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith(("#", "-", "http://", "https://")) or line.startswith("["):
            continue
        line = line.split(" #", 1)[0].strip()
        match = re.match(r"^([A-Za-z0-9_.-]+)\s*(.*)$", line)
        if not match:
            continue
        package, spec = match.groups()
        key = re.sub(r"[-_.]+", "-", package).lower()
        if key in seen:
            findings.append(Finding("OGX-DEP-002", "Duplicate Python requirement", "low", "dependencies",
                f"Package '{package}' appears more than once in this requirements file.",
                relative_path(path, root), number, package,
                "Consolidate the requirement and make version constraints consistent.", "medium"))
        else:
            seen[key] = number
        if not spec.strip() or spec.strip().startswith((";", "#")):
            findings.append(Finding("OGX-DEP-001", "Unpinned Python dependency", "low", "dependencies",
                f"Package '{package}' has no visible version constraint in this requirements file.",
                relative_path(path, root), number, package,
                "Use a reviewed version range and a reproducible lock/constraints workflow where appropriate.", "medium"))
    return findings


def _parse_package_json(path: Path, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return [Finding("OGX-DEP-010", "Invalid package.json", "medium", "dependencies",
                        "package.json could not be parsed as JSON.", relative_path(path, root),
                        recommendation="Validate the JSON syntax and rerun the scan.", confidence="high")]
    for section in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        deps = data.get(section, {})
        if not isinstance(deps, dict):
            continue
        for name, version in deps.items():
            if version in ("*", "latest", "") or not isinstance(version, str):
                findings.append(Finding("OGX-DEP-011", "Unbounded JavaScript dependency", "low", "dependencies",
                    f"Dependency '{name}' in {section} uses an unbounded or invalid version declaration.",
                    relative_path(path, root), evidence=f"{name}: {version!r}",
                    recommendation="Use an intentional version range and commit the matching package-manager lockfile.", confidence="medium"))
    return findings


def _structured_syntax_findings(path: Path, root: Path, text: str) -> list[Finding]:
    """Parse common structured manifests with the Python standard library."""
    suffix = path.suffix.lower()
    name = path.name.lower()
    rel = relative_path(path, root)
    if suffix == ".json" and name not in {"package.json", "tsconfig.json", "jsconfig.json", "launch.json", "tasks.json", "settings.json"}:
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            return [Finding("OGX-SYNTAX-JSON", "Invalid JSON syntax", "high", "syntax",
                            "The JSON parser rejected this file.", rel, exc.lineno,
                            recommendation="Correct the JSON syntax and validate the file with the project's schema or tooling.", confidence="high")]
    if suffix == ".toml":
        try:
            tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            line = getattr(exc, "lineno", None)
            return [Finding("OGX-SYNTAX-TOML", "Invalid TOML syntax", "high", "syntax",
                            "The TOML parser rejected this file.", rel, line,
                            recommendation="Correct the TOML syntax and rerun the manifest validation.", confidence="high")]
    if suffix in {".xml", ".csproj", ".fsproj", ".vbproj"}:
        try:
            ET.fromstring(text)
        except ET.ParseError as exc:
            line = exc.position[0] if getattr(exc, "position", None) else None
            return [Finding("OGX-SYNTAX-XML", "Invalid XML syntax", "high", "syntax",
                            "The XML parser rejected this document.", rel, line,
                            recommendation="Correct malformed XML tags/attributes and validate the project file with its native build tooling.", confidence="high")]
    return []


def _manifest_findings(root: Path, files: list[Path]) -> list[Finding]:
    findings: list[Finding] = []
    package_jsons = [p for p in files if p.name == "package.json"]
    for path in files:
        if path.name.startswith("requirements") and path.suffix == ".txt":
            findings.extend(_parse_requirements(path, root))
        elif path.name == "package.json":
            findings.extend(_parse_package_json(path, root))
    lock_names = {"package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lock", "bun.lockb"}
    for package_file in package_jsons:
        sibling_names = {p.name for p in files if p.parent == package_file.parent}
        if not (sibling_names & lock_names):
            findings.append(Finding("OGX-DEP-012", "JavaScript lockfile not detected", "medium", "dependencies",
                "package.json exists, but no common JavaScript lockfile was detected beside this manifest.",
                relative_path(package_file, root), recommendation="Commit the lockfile for the package manager used by this project/package.", confidence="medium"))
    return findings


def scan_repository(root: Path, config: ScanConfig | None = None) -> ScanReport:
    root = root.expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"Project directory does not exist: {root}")
    config = config or load_config()
    started = utc_now()
    files, skipped, warnings = _iter_files(root, config)
    profile = detect_project(root, files)
    findings = _manifest_findings(root, files)
    examined = 0
    for path in files:
        is_env_file = path.name == ".env" or path.name.startswith(".env.")
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in {"Dockerfile", "Makefile", ".gitignore"} and not is_env_file:
            skipped += 1
            continue
        try:
            raw = path.read_bytes()
            if b"\x00" in raw[:8192]:
                skipped += 1
                continue
            text = raw.decode("utf-8-sig", errors="replace")
        except OSError:
            skipped += 1
            continue
        examined += 1
        findings.extend(_scan_secrets(path, root, text))
        findings.extend(_structured_syntax_findings(path, root, text))
        if path.suffix.lower() == ".py":
            try:
                ast.parse(text, filename=relative_path(path, root))
            except SyntaxError as exc:
                findings.append(Finding("OGX-BUG-001", "Python syntax error", "high", "bugs",
                    f"Python parser rejected this file: {exc.msg}", relative_path(path, root), exc.lineno,
                    recommendation="Fix the syntax error, then run the project's test and lint commands.", confidence="high"))
        if path.suffix.lower() in {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".java", ".kt", ".go", ".rs", ".php", ".rb", ".cs", ".swift", ".c", ".h", ".cpp", ".hpp", ".dart", ".scala", ".sc", ".ex", ".exs", ".erl", ".hrl", ".lua", ".pl", ".r", ".jl", ".hs", ".lhs", ".sol", ".clj", ".cljs", ".cr", ".tf", ".proto", ".vue", ".svelte"}:
            findings.extend(_scan_code_patterns(path, root, text))
        basename = path.name.lower()
        if basename in {"dockerfile", "containerfile"}:
            if re.search(r"(?im)^\s*FROM\s+[^\s:]+:latest\b", text):
                findings.append(Finding("OGX-INFRA-001", "Unpinned container base image", "medium", "infrastructure",
                    "A container build uses a :latest base image tag, which can make builds non-reproducible.",
                    relative_path(path, root), recommendation="Pin a reviewed immutable image tag or digest and update it through a controlled process.", confidence="high"))
            if re.search(r"(?im)^\s*USER\s+", text) is None:
                findings.append(Finding("OGX-INFRA-002", "Container may run as root", "medium", "infrastructure",
                    "No USER instruction was detected in the Dockerfile; the final image may run as root.",
                    relative_path(path, root), recommendation="Use a dedicated non-root runtime user where supported and verify file permissions.", confidence="low"))
        if path.name in {".gitignore"} and not re.search(r"(?m)^\.env(?:\s|$|/)", text):
            if (root / ".env").exists():
                findings.append(Finding("OGX-SEC-003", "Environment file may not be ignored", "medium", "secrets",
                    "A root .env file exists but .gitignore does not clearly ignore it.", relative_path(path, root),
                    recommendation="Add an appropriate .env ignore rule and verify that no credentials are already tracked.", confidence="medium"))
        if ".github/workflows/" in relative_path(path, root) and re.search(r"\$\{\{\s*github\.event\.(?:issue|comment|review|pull_request)\.(?:title|body|comment|head\.ref)", text):
            if re.search(r"(?im)^\s*run:\s*.*\$\{\{", text):
                findings.append(Finding("OGX-CI-001", "Potential untrusted GitHub expression in run step", "high", "ci-cd",
                    "A workflow may interpolate untrusted event data directly into a shell run command.", relative_path(path, root),
                    recommendation="Pass untrusted event values through environment variables and quote/validate them; avoid direct expression interpolation in shell scripts.", confidence="medium"))

    # Stable de-duplication protects reports from repeated pattern hits at the same location.
    unique: dict[tuple[str, str | None, int | None, str], Finding] = {}
    for finding in findings:
        key = (finding.rule_id, finding.path, finding.line, finding.message)
        unique[key] = finding
    ordered = sorted(unique.values(), key=lambda f: ({"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}.get(f.severity, 5), f.path or "", f.line or 0, f.rule_id))
    capabilities = {
        "project_detection": "available",
        "dependency_manifest_review": "manifest heuristics and SBOM inventory; no full resolver",
        "secret_pattern_scan": "heuristic; findings require verification",
        "secure_coding_patterns": "heuristic; context and data-flow analysis are not implemented",
        "known_vulnerability_database": "separate opt-in OSV.dev exact-version query; no full resolver or transitive match",
        "license_compliance": "license file detection only; no legal conclusion or full SPDX resolver",
        "runtime_testing": "disabled by default; OGX scanner never executes target code",
        "ai_remediation": "proposals only; no automatic source edits",
    }
    return ScanReport(
        tool="OMEGA GUARDIAN X", version=__version__, scan_id=uuid.uuid4().hex[:12],
        started_at=started, finished_at=utc_now(), project=profile,
        files_examined=examined, files_skipped=skipped, findings=ordered,
        capabilities=capabilities, warnings=warnings,
    )
