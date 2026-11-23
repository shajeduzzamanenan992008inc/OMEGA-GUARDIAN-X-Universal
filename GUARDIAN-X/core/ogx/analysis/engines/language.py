"""Spec-driven language engines.

The generic engine performs bounded, static analysis only:

* extension/manifest detection
* standard-library structural parsing where a safe parser exists
* declaration-level dependency inventory (never resolution/installation)
* test/build discovery (never execution)
* shared secret and secure-coding heuristics
* infrastructure/automation checks for declarative technologies

Language-specific depth is declared honestly through capability statuses.
"""

from __future__ import annotations

import ast
import fnmatch
import json
import re
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from ogx.kernel.models import Finding
from ogx.kernel.rules import (
    CODE_CHECKS,
    DOCKER_CHECKS,
    KUBERNETES_CHECKS,
    POWERSHELL_CHECKS,
    SECRET_PATTERNS,
    SHELL_CHECKS,
    TERRAFORM_CHECKS,
    is_placeholder,
    line_for,
    redact_evidence,
)
from ogx.kernel.workspace import Workspace, WorkspaceError, strip_jsonc
from ogx.analysis.engines.build import discover_builds, discover_tests
from ogx.analysis.engines.contract import BaseEngine, Capability, CapabilityStatus, EngineContext
from ogx.analysis.engines.language_specs import LanguageSpec, SPECS, spec_by_key

MAX_ANALYZED_FILES = 1500
MAX_FINDINGS = 400
MAX_MANIFEST_BYTES = 4_000_000
MAX_ANALYSIS_BYTES = 64_000_000


@dataclass(frozen=True, slots=True)
class Component:
    ecosystem: str
    name: str
    constraint: str
    source: str
    declared: bool = True
    resolved: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ecosystem": self.ecosystem, "name": self.name, "constraint": self.constraint,
            "source": self.source, "declared": self.declared, "resolved": self.resolved,
            "version": "UNKNOWN",
        }


def _rule(spec: LanguageSpec, suffix: str) -> str:
    key = re.sub(r"[^A-Z0-9]+", "-", spec.key.upper())
    return f"OGX-{key}-{suffix}"


def _finding(rule_id: str, title: str, severity: str, category: str, message: str,
             path: str | None = None, line: int | None = None, evidence: str | None = None,
             recommendation: str | None = None, confidence: str = "medium") -> Finding:
    return Finding(rule_id, title, severity, category, message, path, line,
                   redact_evidence(evidence) if evidence else None,
                   recommendation, confidence)


def _python_files(workspace: Workspace, spec: LanguageSpec) -> list[str]:
    found: list[str] = []
    for entry in workspace.files:
        name = Path(entry.path).name
        suffix = Path(entry.path).suffix.lower()
        if suffix in {item.lower() for item in spec.extensions}:
            found.append(entry.path)
        elif name in spec.file_names:
            found.append(entry.path)
        elif name in spec.manifests:
            found.append(entry.path)
        elif any(fnmatch.fnmatchcase(entry.path, pattern) for pattern in spec.manifest_globs):
            found.append(entry.path)
    return sorted(set(found))


def _read_bounded(workspace: Workspace, rel: str, *, limit: int = MAX_MANIFEST_BYTES) -> str | None:
    entry = workspace.entry(rel)
    if entry is None or entry.size > limit:
        return None
    try:
        return workspace.read_text(rel, max_bytes=limit)
    except WorkspaceError:
        return None


def _parse_json_text(text: str, *, jsonc: bool = False) -> Any:
    return json.loads(strip_jsonc(text) if jsonc else text)


def _manifest_parser_available(name: str) -> bool:
    """Return True when a declaration-only dependency parser is registered."""
    if name.startswith("requirements") and name.endswith(".txt"):
        return True
    if name in {
        "package.json", "pyproject.toml", "pom.xml", "build.gradle", "build.gradle.kts",
        "Cargo.toml", "go.mod", "composer.json", "Gemfile", "pubspec.yaml", "Package.swift",
        "Project.toml", "DESCRIPTION", "cpanfile",
    }:
        return True
    return Path(name).suffix.lower() in {".csproj", ".fsproj", ".vbproj", ".rockspec"}


def _c_like_balanced(text: str) -> bool:
    """Conservative delimiter balance after stripping strings/comments.

    Returns True when braces/parentheses/brackets are balanced and never go
    negative.  This is a heuristic, not a language parser.
    """
    stack: list[str] = []
    pairs = {")": "(", "]": "[", "}": "{"}
    i = 0
    length = len(text)
    while i < length:
        char = text[i]
        nxt = text[i + 1] if i + 1 < length else ""
        if char == "/" and nxt == "/":
            i = text.find("\n", i)
            if i == -1:
                break
            continue
        if char == "/" and nxt == "*":
            end = text.find("*/", i + 2)
            i = length if end == -1 else end + 2
            continue
        if char == "#" and i == 0:
            # Lua/Perl-style line comment is only safe at the start of a line.
            i = text.find("\n", i)
            if i == -1:
                break
            continue
        if text.startswith('"""', i):
            end = text.find('"""', i + 3)
            i = length if end == -1 else end + 3
            continue
        if text.startswith("'''", i):
            end = text.find("'''", i + 3)
            i = length if end == -1 else end + 3
            continue
        if char in "\"'`":
            quote = char
            i += 1
            while i < length:
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        if char in "([{":
            stack.append(char)
        elif char in ")]}":
            if not stack or stack[-1] != pairs[char]:
                return False
            stack.pop()
        elif char == "r" and nxt in "#\"":
            # Rust raw string: r"..." or r#"..."#.
            hash_count = 0
            j = i + 1
            while j < length and text[j] == "#":
                hash_count += 1
                j += 1
            if j < length and text[j] == '"':
                terminator = '"' + "#" * hash_count
                end = text.find(terminator, j + 1)
                i = length if end == -1 else end + len(terminator)
                continue
        i += 1
    return not stack


def _yaml_tab_finding(spec: LanguageSpec, rel: str, text: str) -> list[Finding]:
    for number, line in enumerate(text.splitlines(), 1):
        if re.match(r"^[ \t]*\t", line) and line.strip() and not line.lstrip().startswith("#"):
            return [_finding("OGX-SYNTAX-YAML", "Tab used for YAML indentation", "medium", "syntax",
                             "YAML requires spaces for indentation; a tab was detected in leading whitespace.",
                             rel, number, "leading tab", "Replace leading tabs with spaces and validate with a YAML parser.",
                             "high")]
    return []


def _secret_findings(rel: str, text: str) -> list[Finding]:
    findings: list[Finding] = []
    for rule_id, title, pattern in SECRET_PATTERNS:
        for match in pattern.finditer(text):
            if rule_id == "OGX-SEC-001":
                value = match.group(3)
                if is_placeholder(value):
                    continue
                evidence = f"{match.group(1)}=<redacted>"
            else:
                evidence = "Private-key PEM header detected; key contents were not captured."
            findings.append(Finding(
                rule_id, title, "high", "secrets",
                "A file may contain a hard-coded credential. Verify whether it is real and rotate it if exposed.",
                rel, line_for(text, match.start()), evidence,
                "Move credentials to an approved secret manager or environment injection; remove the exposed value and rotate it.",
                "medium" if rule_id == "OGX-SEC-001" else "high",
            ))
    return findings


def _matches_checks(rel: str, text: str, checks: Iterable[tuple]) -> list[Finding]:
    findings: list[Finding] = []
    for rule_id, title, pattern, severity, message, recommendation in checks:
        for match in pattern.finditer(text):
            findings.append(Finding(rule_id, title, severity, "secure-coding", message, rel,
                                    line_for(text, match.start()), match.group(0),
                                    recommendation, "medium"))
    return findings


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


class LanguageEngine(BaseEngine):
    """One engine instance per technology spec."""

    category = "language"

    def __init__(self, spec: LanguageSpec):
        self.spec = spec
        self.engine_id = f"lang-{spec.key}"
        self.engine_version = "1.0.0"
        self.display_name = f"{spec.name} language engine"
        self.language_keys = (spec.key,)

    # -- detection --------------------------------------------------------
    def _candidate_files(self, context: EngineContext) -> list[str]:
        return _python_files(context.workspace, self.spec)

    def detect(self, context: EngineContext) -> bool:
        files = self._candidate_files(context)
        if self.spec.detection_mode == "content":
            return any(self._looks_like_kubernetes(context, rel) for rel in files[:200])
        return bool(files)

    def _looks_like_kubernetes(self, context: EngineContext, rel: str) -> bool:
        entry = context.workspace.entry(rel)
        if entry is None or entry.size > 512_000:
            return False
        text = _read_bounded(context.workspace, rel, limit=512_000)
        if not text:
            return False
        head = text[:8192]
        return bool(re.search(r"(?m)^\s*apiVersion\s*:", head) and re.search(r"(?m)^\s*kind\s*:", head))

    def _signals(self, context: EngineContext) -> dict[str, Any]:
        files = self._candidate_files(context)
        return {
            "matched_files": len(files),
            "sample_files": files[:20],
            "manifests": [rel for rel in files if Path(rel).name in self.spec.manifests][:20],
            "parser": self.spec.parser,
            "detection_mode": self.spec.detection_mode,
        }

    # -- capabilities -----------------------------------------------------
    def capabilities(self, context: EngineContext) -> dict[str, Capability]:
        files = self._candidate_files(context)
        manifests = sorted({rel for rel in files if self._is_manifest(rel)})
        relevant_builds = [item for item in discover_builds(context.workspace)
                           if item["adapter_id"] in self.spec.build_adapter_ids]
        tests = discover_tests(context.workspace, list(self.spec.test_languages)) if self.spec.test_languages else {}
        test_found = any(item.get("discovered") for item in tests.get("languages", {}).values())

        analyze_status = CapabilityStatus.PARTIAL
        analyze_detail = {
            "python-ast": "Standard-library AST syntax/import analysis; no type inference or data flow.",
            "c-like": "Delimiter/string-aware structural checks and pattern rules; no compiler-grade parser.",
            "xml": "DTD-rejected XML parse plus pattern rules.",
            "json": "JSON/JSONC parse plus rules.",
            "toml": "Standard-library TOML parse plus rules.",
            "yaml": "No YAML parser dependency; structural checks and pattern rules only.",
            "text": "Inventory plus text/pattern rules; no language parser.",
        }.get(self.spec.parser, "Bounded static heuristics.")
        if self.spec.detection_mode == "content":
            analyze_status = CapabilityStatus.PARTIAL
            analyze_detail = "Content-based manifest detection plus Kubernetes rule checks; no cluster access."

        parseable_manifests = [rel for rel in manifests if _manifest_parser_available(Path(rel).name)]
        if not self.spec.manifests and not self.spec.manifest_globs:
            dependency_status = CapabilityStatus.UNSUPPORTED
            dependency_detail = "No dependency manifest is associated with this technology."
        elif parseable_manifests:
            dependency_status = CapabilityStatus.PARTIAL
            dependency_detail = "Declared manifest constraints only; no resolver, lockfile verification, transitive graph or CVE lookup."
        elif manifests:
            dependency_status = CapabilityStatus.UNSUPPORTED
            dependency_detail = "The detected manifest is a technology marker; no dependency parser is registered for it."
        else:
            dependency_status = CapabilityStatus.NOT_CONFIGURED
            dependency_detail = "No associated manifest was found in this project."

        if not self.spec.build_adapter_ids:
            build_status = CapabilityStatus.UNSUPPORTED
            build_detail = "No build adapter is registered for this technology."
        elif relevant_builds:
            available = [item for item in relevant_builds if item["toolchain_available"]]
            build_status = CapabilityStatus.PARTIAL if available else CapabilityStatus.NOT_CONFIGURED
            build_detail = ("Build/test command discovery only; execution requires an approved isolated worker."
                            if available else "Manifest detected but not all declared local tools are available; nothing was executed.")
        else:
            build_status = CapabilityStatus.NOT_CONFIGURED
            build_detail = "No recognized build manifest was found; no build command was inferred."

        if not self.spec.test_languages:
            test_status = CapabilityStatus.UNSUPPORTED
            test_detail = "No test-discovery pattern is registered for this technology."
        elif test_found:
            test_status = CapabilityStatus.PARTIAL
            test_detail = "Test files were discovered; no test was executed."
        else:
            test_status = CapabilityStatus.NOT_CONFIGURED
            test_detail = "No conventional test files were discovered; execution was not attempted."

        diagnose_status = CapabilityStatus.PARTIAL if self.spec.parser != "text" else CapabilityStatus.UNSUPPORTED
        diagnose_detail = ("Static structural diagnostics only; runtime logs are handled by the separate log diagnostics command."
                           if diagnose_status is CapabilityStatus.PARTIAL else "No structural diagnostic parser is registered.")

        return {
            "detect": Capability(CapabilityStatus.SUPPORTED if files else CapabilityStatus.UNSUPPORTED,
                                 f"{len(files)} matching file(s) by extension/name/manifest."),
            "analyze": Capability(analyze_status, analyze_detail),
            "dependencies": Capability(dependency_status, dependency_detail),
            "build": Capability(build_status, build_detail),
            "test": Capability(test_status, test_detail),
            "diagnose": Capability(diagnose_status, diagnose_detail),
        }

    def _is_manifest(self, rel: str) -> bool:
        name = Path(rel).name
        if name in self.spec.manifests:
            return True
        return any(fnmatch.fnmatchcase(rel, pattern) or fnmatch.fnmatchcase(name, pattern)
                   for pattern in self.spec.manifest_globs)

    # -- analysis ---------------------------------------------------------
    def analyze(self, context: EngineContext) -> dict[str, Any]:
        workspace = context.workspace
        files = self._candidate_files(context)
        analyzed = files[:MAX_ANALYZED_FILES]
        warnings: list[str] = []
        if len(files) > MAX_ANALYZED_FILES:
            warnings.append(f"Analysis limited to {MAX_ANALYZED_FILES} of {len(files)} matching files.")
        findings: list[Finding] = []
        parsed = 0
        bytes_read = 0
        test_files = 0
        skipped = 0
        for rel in analyzed:
            entry = workspace.entry(rel)
            if entry is None:
                continue
            if entry.size > context.config.max_file_bytes:
                skipped += 1
                continue
            if bytes_read + entry.size > MAX_ANALYSIS_BYTES:
                warnings.append(f"Analysis stopped at the {MAX_ANALYSIS_BYTES}-byte engine budget; results are partial.")
                break
            try:
                text = workspace.read_text(rel)
            except WorkspaceError:
                skipped += 1
                continue
            parsed += 1
            bytes_read += entry.size
            if self._looks_like_test(rel):
                test_files += 1
            findings.extend(_secret_findings(rel, text))
            findings.extend(self._structured_findings(rel, text, warnings))
            if self.spec.parser in {"c-like", "python-ast"} and not self._is_manifest(rel):
                findings.extend(_matches_checks(rel, text, CODE_CHECKS))
                if self.spec.parser == "c-like" and not _c_like_balanced(text):
                    findings.append(_finding(
                        _rule(self.spec, "BALANCE"), "Possible unbalanced code delimiters", "low",
                        "syntax", "Braces, parentheses or brackets appear unbalanced after string/comment stripping. "
                                  "This is a heuristic and may reflect a syntax error or an unsupported language construct.",
                        rel, evidence="delimiter stack did not return to zero",
                        recommendation="Compile or lint the file with the project's native toolchain to confirm.", confidence="low"))
            findings.extend(self._technology_findings(rel, text))

        if (self.spec.test_languages and not test_files and files
                and self.spec.key in {"python", "javascript", "typescript", "java", "go", "rust",
                                      "csharp", "ruby", "php", "swift", "dart", "scala", "elixir",
                                      "erlang", "haskell"}):
            findings.append(_finding(
                _rule(self.spec, "TESTS"), "No conventional test files detected", "info", "testing",
                f"No test file matching registered {self.spec.name} patterns was found. This does not prove that tests are absent or that the project is untested.",
                recommendation="Confirm the project's actual test layout; add regression tests for confirmed defects.", confidence="low"))
        if len(findings) > MAX_FINDINGS:
            warnings.append(f"Findings truncated to {MAX_FINDINGS} for this engine.")
            findings = findings[:MAX_FINDINGS]

        return {
            "facts": {
                "technology": self.spec.name,
                "category": self.spec.category,
                "matched_files": len(files),
                "files_analyzed": parsed,
                "files_skipped": skipped,
                "bytes_analyzed": bytes_read,
                "test_files_detected": test_files,
                "parser": self.spec.parser,
                "detection_mode": self.spec.detection_mode,
                "limitations": list(self.spec.notes),
            },
            "findings": findings,
            "warnings": warnings,
        }

    def _structured_findings(self, rel: str, text: str, warnings: list[str]) -> list[Finding]:
        suffix = Path(rel).suffix.lower()
        name = Path(rel).name.casefold()
        if suffix == ".py":
            try:
                ast.parse(text, filename=rel)
            except SyntaxError as exc:
                return [Finding("OGX-BUG-001", "Python syntax error", "high", "bugs",
                                f"Python parser rejected this file: {exc.msg}", rel, exc.lineno,
                                recommendation="Fix the syntax error, then run the project's test and lint commands.",
                                confidence="high")]
            return []
        if suffix in {".json", ".jsonc"}:
            if name in {".eslintrc", "tsconfig.json", "jsconfig.json"} or suffix == ".jsonc":
                # JSONC leniency: strip comments/trailing commas. JSON5-only
                # syntax (unquoted keys) is not claimed to parse.
                try:
                    _parse_json_text(text, jsonc=True)
                except json.JSONDecodeError as exc:
                    return [Finding("OGX-SYNTAX-JSON", "Invalid JSON/JSONC syntax", "high", "syntax",
                                    "The JSON-compatible parser rejected this file.", rel, exc.lineno,
                                    recommendation="Correct the syntax and validate with the project's configuration parser.",
                                    confidence="high")]
                return []
            try:
                json.loads(text)
            except json.JSONDecodeError as exc:
                return [Finding("OGX-SYNTAX-JSON", "Invalid JSON syntax", "high", "syntax",
                                "The JSON parser rejected this file.", rel, exc.lineno,
                                recommendation="Correct the JSON syntax and validate the file with the project's schema or tooling.",
                                confidence="high")]
            return []
        if suffix == ".toml":
            try:
                tomllib.loads(text)
            except tomllib.TOMLDecodeError as exc:
                line = getattr(exc, "lineno", None)
                return [Finding("OGX-SYNTAX-TOML", "Invalid TOML syntax", "high", "syntax",
                                "The TOML parser rejected this file.", rel, line,
                                recommendation="Correct the TOML syntax and rerun manifest validation.", confidence="high")]
            return []
        if suffix in {".xml", ".xsd", ".xsl", ".xslt", ".svg", ".csproj", ".fsproj", ".vbproj"}:
            if re.search(r"<!(?:DOCTYPE|ENTITY)\b", text, re.IGNORECASE):
                return [Finding("OGX-SYNTAX-XML", "XML DTD/entity declaration rejected", "medium", "syntax",
                                "XML files containing DTD/entity declarations are not parsed for safety.",
                                rel, recommendation="Remove DTD/entity declarations or review the file with a hardened XML tool.",
                                confidence="high")]
            try:
                ET.fromstring(text)
            except ET.ParseError as exc:
                line = exc.position[0] if getattr(exc, "position", None) else None
                return [Finding("OGX-SYNTAX-XML", "Invalid XML syntax", "high", "syntax",
                                "The XML parser rejected this document.", rel, line,
                                recommendation="Correct malformed XML tags/attributes and validate with native tooling.",
                                confidence="high")]
            return []
        if suffix in {".yaml", ".yml"}:
            return _yaml_tab_finding(self.spec, rel, text)
        return []

    def _looks_like_test(self, rel: str) -> bool:
        path = rel.replace("\\", "/")
        name = Path(path).name
        if re.search(r"(^|/)(tests?|__tests__|spec|androidTest)/", path):
            return True
        return bool(re.search(r"(?:^test_.*|.*_test\.\w+|.*\.(?:test|spec)\.\w+)$", name))

    def _technology_findings(self, rel: str, text: str) -> list[Finding]:
        findings: list[Finding] = []
        if self.spec.key == "dockerfile":
            findings.extend(_matches_checks(rel, text, DOCKER_CHECKS))
            if re.search(r"(?im)^\s*USER\s+", text) is None:
                findings.append(_finding("OGX-INFRA-002", "Container may run as root", "medium", "infrastructure",
                                         "No USER instruction was detected in the Dockerfile; the final image may run as root.",
                                         rel, recommendation="Use a dedicated non-root runtime user where supported and verify file permissions.",
                                         confidence="low"))
        elif self.spec.key == "shell":
            findings.extend(_matches_checks(rel, text, SHELL_CHECKS))
        elif self.spec.key == "powershell":
            findings.extend(_matches_checks(rel, text, POWERSHELL_CHECKS))
        elif self.spec.key == "kubernetes":
            findings.extend(_matches_checks(rel, text, KUBERNETES_CHECKS))
        elif self.spec.key == "terraform":
            findings.extend(_matches_checks(rel, text, TERRAFORM_CHECKS))
        elif self.spec.key == "ci":
            if (re.search(r"\$\{\{\s*github\.event\.(?:issue|comment|review|pull_request)\.(?:title|body|comment|head\.ref)", text)
                    and re.search(r"(?im)^\s*run:\s*.*\$\{\{", text)):
                findings.append(_finding("OGX-CI-001", "Potential untrusted GitHub expression in run step", "high", "ci-cd",
                                         "A workflow may interpolate untrusted event data directly into a shell run command.",
                                         rel, recommendation="Pass untrusted event values through environment variables and quote/validate them; avoid direct expression interpolation in shell scripts.",
                                         confidence="medium"))
        return findings

    # -- dependency inventory --------------------------------------------
    def dependencies(self, context: EngineContext) -> dict[str, Any]:
        workspace = context.workspace
        manifests = sorted({rel for rel in self._candidate_files(context) if self._is_manifest(rel)})
        components: list[Component] = []
        parsed_manifests: list[str] = []
        unsupported_manifests: list[str] = []
        warnings: list[str] = []
        findings: list[Finding] = []
        for rel in manifests:
            entry = workspace.entry(rel)
            if entry is None or entry.size > MAX_MANIFEST_BYTES:
                warnings.append(f"Skipped oversized manifest: {rel}")
                continue
            if not _manifest_parser_available(Path(rel).name):
                unsupported_manifests.append(rel)
                continue
            try:
                parsed = self._parse_manifest(context, rel)
            except (WorkspaceError, ValueError, KeyError, TypeError, json.JSONDecodeError,
                    tomllib.TOMLDecodeError, ET.ParseError) as exc:
                warnings.append(f"Could not parse dependency manifest {rel}: {type(exc).__name__}")
                continue
            if parsed is None:
                unsupported_manifests.append(rel)
                continue
            parsed_manifests.append(rel)
            components.extend(parsed)
        components = _dedupe_components(components)

        for component in components:
            constraint = component.constraint.strip()
            if component.ecosystem in {"npm", "NuGet"} and constraint in {"*", "latest", ""}:
                findings.append(_finding(
                    _rule(self.spec, "DEP-UNPINNED"), "Unbounded dependency declaration", "low", "dependencies",
                    f"Dependency '{component.name}' uses an unbounded version declaration.",
                    component.source, evidence=f"{component.name}: {constraint or 'missing'}",
                    recommendation="Use an intentional version range and commit the matching lockfile where appropriate.",
                    confidence="medium"))
            if component.ecosystem == "gradle" and constraint.endswith("+"):
                findings.append(_finding(
                    _rule(self.spec, "DEP-DYNAMIC"), "Dynamic Gradle dependency version", "medium", "dependencies",
                    f"Dependency '{component.name}' uses a dynamic '+' version, which can change without review.",
                    component.source, evidence=f"{component.name}:{constraint}",
                    recommendation="Pin a reviewed version and update it through dependency automation.",
                    confidence="high"))
            if component.ecosystem == "pip" and not constraint:
                findings.append(_finding(
                    _rule(self.spec, "DEP-UNPINNED"), "Unpinned Python dependency", "low", "dependencies",
                    f"Package '{component.name}' has no visible version constraint.",
                    component.source, evidence=component.name,
                    recommendation="Use a reviewed version range and a reproducible lock/constraints workflow.",
                    confidence="medium"))

        lockfiles_present = [rel for rel in self._candidate_files(context)
                             if Path(rel).name in set(self.spec.lockfiles)]
        if manifests and self.spec.lockfiles and not lockfiles_present:
            findings.append(_finding(
                _rule(self.spec, "LOCK"), "Lockfile not detected", "low", "dependencies",
                "A dependency manifest exists but no conventional lockfile was detected. This is not always a defect (for example, reusable libraries).",
                manifests[0], recommendation="Commit the ecosystem-supported lockfile when reproducible application builds are required.",
                confidence="low"))

        return {
            "components": [component.to_dict() for component in components],
            "declared_count": len(components),
            "parsed_manifests": parsed_manifests,
            "unsupported_manifests": unsupported_manifests,
            "lockfiles_present": lockfiles_present,
            "resolved": False,
            "transitive": False,
            "vulnerability_data": "not_queried",
            "limitations": ["Declared constraints only; installed/resolved versions are unknown.",
                            "No transitive dependency graph is produced.",
                            "No vulnerability database is queried by this operation."],
            "findings": findings,
            "warnings": warnings,
        }

    def _parse_manifest(self, context: EngineContext, rel: str) -> list[Component] | None:
        workspace = context.workspace
        name = Path(rel).name
        suffix = Path(rel).suffix.lower()
        text = _read_bounded(workspace, rel)
        if text is None:
            return []
        if name.startswith("requirements") and suffix == ".txt":
            return _parse_requirements(text, rel)
        if name == "package.json":
            return _parse_package_json(text, rel)
        if name == "pyproject.toml":
            return _parse_pyproject(text, rel)
        if name == "pom.xml":
            return _parse_pom(text, rel)
        if name in {"build.gradle", "build.gradle.kts"}:
            return _parse_gradle(text, rel)
        if name == "Cargo.toml":
            return _parse_cargo(text, rel)
        if name == "go.mod":
            return _parse_go_mod(text, rel)
        if name in {"*.csproj", "*.fsproj", "*.vbproj"} or suffix in {".csproj", ".fsproj", ".vbproj"}:
            return _parse_csproj(text, rel)
        if name == "composer.json":
            return _parse_composer(text, rel)
        if name == "Gemfile":
            return _parse_gemfile(text, rel)
        if name == "pubspec.yaml":
            return _parse_pubspec(text, rel)
        if name == "Package.swift":
            return _parse_package_swift(text, rel)
        if name == "Project.toml":
            return _parse_julia_project(text, rel)
        if name == "DESCRIPTION":
            return _parse_r_description(text, rel)
        if name == "cpanfile":
            return _parse_cpanfile(text, rel)
        if suffix == ".rockspec":
            return _parse_rockspec(text, rel)
        return None

    # -- build / test / diagnose -----------------------------------------
    def build(self, context: EngineContext) -> dict[str, Any]:
        discovered = [item for item in discover_builds(context.workspace)
                      if item["adapter_id"] in self.spec.build_adapter_ids]
        return {
            "discovered": bool(discovered),
            "adapters": discovered,
            "executed": False,
            "execution_status": "not_configured",
            "note": "Build discovery only. No command, wrapper, package manager or project script was executed.",
        }

    def test(self, context: EngineContext) -> dict[str, Any]:
        result = discover_tests(context.workspace, list(self.spec.test_languages))
        return {**result, "executed": False, "execution_status": "not_configured"}

    def diagnose(self, context: EngineContext) -> dict[str, Any]:
        return {
            "diagnostics": [],
            "structural_checks": ["syntax", "dependency declarations", "delimiter balance"]
                                  if self.spec.parser in {"python-ast", "c-like", "xml", "json"} else ["inventory"],
            "runtime_logs": "not_analyzed",
            "note": "No project code was executed. Use `ogx diagnose` for existing log files.",
        }


# ---------------------------------------------------------------------------
# Dependency manifest parsers (declaration-only)
# ---------------------------------------------------------------------------

def _requirement_component(line: str, source: str) -> Component | None:
    line = line.strip()
    if not line or line.startswith(("#", "-", "http://", "https://", "git+", "[")):
        return None
    line = line.split(" #", 1)[0].strip()
    match = re.match(r"^([A-Za-z0-9_.-]+)\s*(.*)$", line)
    if not match:
        return None
    package, constraint = match.groups()
    constraint = constraint.split(";", 1)[0].strip()
    return Component("pip", package, constraint, source)


def _parse_requirements(text: str, rel: str) -> list[Component]:
    return [component for line in text.splitlines()
            if (component := _requirement_component(line, rel)) is not None]


def _parse_package_json(text: str, rel: str) -> list[Component]:
    data = json.loads(text)
    components: list[Component] = []
    for section in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        deps = data.get(section, {})
        if not isinstance(deps, dict):
            continue
        for name, constraint in deps.items():
            if isinstance(constraint, str):
                components.append(Component("npm", str(name), constraint, rel))
    return components


def _parse_pyproject(text: str, rel: str) -> list[Component]:
    data = tomllib.loads(text)
    components: list[Component] = []
    project = data.get("project", {})
    if isinstance(project, dict):
        for item in project.get("dependencies", []) or []:
            if isinstance(item, str):
                component = _requirement_component(item, rel)
                if component:
                    components.append(component)
        optional = project.get("optional-dependencies", {})
        if isinstance(optional, dict):
            for group in optional.values():
                for item in group or []:
                    if isinstance(item, str):
                        component = _requirement_component(item, rel)
                        if component:
                            components.append(component)
    poetry = data.get("tool", {}).get("poetry", {})
    if isinstance(poetry, dict):
        for section in ("dependencies", "dev-dependencies"):
            deps = poetry.get(section, {})
            if isinstance(deps, dict):
                for name, value in deps.items():
                    if str(name).lower() == "python":
                        continue
                    constraint = value if isinstance(value, str) else str(value.get("version", "")) if isinstance(value, dict) else ""
                    components.append(Component("pip", str(name), constraint, rel))
    return components


def _parse_pom(text: str, rel: str) -> list[Component]:
    root = ET.fromstring(text)
    components: list[Component] = []
    for dependency in root.iter():
        if _local(dependency.tag) != "dependency":
            continue
        fields = {_local(child.tag): (child.text or "").strip() for child in dependency}
        group = fields.get("groupId", "")
        artifact = fields.get("artifactId", "")
        if not artifact:
            continue
        scope = fields.get("scope", "compile")
        components.append(Component("maven", f"{group}:{artifact}" if group else artifact,
                                    fields.get("version", ""), rel))
    return components


def _parse_gradle(text: str, rel: str) -> list[Component]:
    components: list[Component] = []
    pattern = re.compile(
        r"(?m)^\s*(implementation|api|compileOnly|runtimeOnly|kapt|ksp|annotationProcessor|"
        r"testImplementation|androidTestImplementation|debugImplementation)\s*\(?\s*"
        r"['\"]([^:'\"]+):([^:'\"]+):([^'\"]+)['\"]"
    )
    for match in pattern.finditer(text):
        components.append(Component("gradle", f"{match.group(2)}:{match.group(3)}",
                                    match.group(4).strip(), rel))
    return components


def _parse_cargo(text: str, rel: str) -> list[Component]:
    data = tomllib.loads(text)
    components: list[Component] = []
    for section in ("dependencies", "dev-dependencies", "build-dependencies"):
        deps = data.get(section, {})
        if isinstance(deps, dict):
            for name, value in deps.items():
                if isinstance(value, str):
                    constraint = value
                elif isinstance(value, dict):
                    constraint = str(value.get("version", value.get("git", value.get("path", ""))))
                else:
                    constraint = ""
                components.append(Component("cargo", name, constraint, rel))
    targets = data.get("target", {})
    if isinstance(targets, dict):
        for target in targets.values():
            if isinstance(target, dict):
                deps = target.get("dependencies", {})
                if isinstance(deps, dict):
                    for name, value in deps.items():
                        constraint = value if isinstance(value, str) else str(value.get("version", "")) if isinstance(value, dict) else ""
                        components.append(Component("cargo", name, constraint, rel))
    return components


def _parse_go_mod(text: str, rel: str) -> list[Component]:
    components: list[Component] = []
    in_block = False
    for raw in text.splitlines():
        line = raw.split("//", 1)[0].strip()
        if not line:
            continue
        if line.startswith("require ("):
            in_block = True
            continue
        if in_block and line == ")":
            in_block = False
            continue
        if line.startswith("require "):
            line = line[len("require "):].strip()
        elif not in_block:
            continue
        parts = line.split()
        if len(parts) >= 2:
            components.append(Component("go", parts[0], parts[1], rel))
    return components


def _parse_csproj(text: str, rel: str) -> list[Component]:
    root = ET.fromstring(text)
    components: list[Component] = []
    for element in root.iter():
        if _local(element.tag) != "PackageReference":
            continue
        name = element.attrib.get("Include") or element.attrib.get("Update")
        if not name:
            continue
        version = element.attrib.get("Version", "")
        if not version:
            version_el = next((child for child in element if _local(child.tag) == "Version"), None)
            version = (version_el.text or "").strip() if version_el is not None else ""
        components.append(Component("NuGet", name, version, rel))
    return components


def _parse_composer(text: str, rel: str) -> list[Component]:
    data = json.loads(text)
    components: list[Component] = []
    for section in ("require", "require-dev"):
        deps = data.get(section, {})
        if not isinstance(deps, dict):
            continue
        for name, constraint in deps.items():
            if str(name).lower() == "php" or str(name).startswith(("ext-", "lib-")):
                continue
            components.append(Component("composer", str(name), str(constraint), rel))
    return components


def _parse_gemfile(text: str, rel: str) -> list[Component]:
    components: list[Component] = []
    for match in re.finditer(r"(?m)^\s*gem\s+['\"]([^'\"]+)['\"](?:\s*,\s*['\"]([^'\"]+)['\"])?", text):
        components.append(Component("rubygems", match.group(1), match.group(2) or "", rel))
    return components


def _parse_pubspec(text: str, rel: str) -> list[Component]:
    components: list[Component] = []
    section: str | None = None
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if not raw.startswith((" ", "\t")):
            key = raw.split(":", 1)[0].strip()
            section = key if key in {"dependencies", "dev_dependencies", "dependency_overrides"} else None
            continue
        if section is None:
            continue
        match = re.match(r"^\s{2}([A-Za-z0-9_.-]+)\s*:\s*(.*)$", raw)
        if match:
            name, constraint = match.groups()
            components.append(Component("pub", name, constraint.strip(), rel))
    return components


def _parse_package_swift(text: str, rel: str) -> list[Component]:
    components: list[Component] = []
    for match in re.finditer(r"\.package\s*\([^)]*url\s*:\s*\"([^\"]+)\"[^)]*?(?:from|exact|upToNextMajor|upToNextMinor)\s*:\s*\"([^\"]+)\"", text, re.DOTALL):
        url, constraint = match.groups()
        name = url.rstrip("/").split("/")[-1].removesuffix(".git")
        components.append(Component("swiftpm", name, constraint, rel))
    return components


def _parse_julia_project(text: str, rel: str) -> list[Component]:
    data = tomllib.loads(text)
    deps = data.get("deps", {})
    return [Component("julia", str(name), "", rel) for name in deps] if isinstance(deps, dict) else []


def _parse_r_description(text: str, rel: str) -> list[Component]:
    components: list[Component] = []
    for line in text.splitlines():
        match = re.match(r"(?i)^(Depends|Imports|Suggests|LinkingTo):\s*(.+)$", line)
        if not match:
            continue
        for item in match.group(2).split(","):
            item = item.strip()
            if not item:
                continue
            name, _, constraint = re.split(r"([<>=!~]+)", item, maxsplit=1) if re.search(r"[<>=!~]", item) else (item, "", "")
            name = name.strip().split(" ")[0]
            if name and name.lower() != "r":
                components.append(Component("cran", name, constraint.strip(), rel))
    return components


def _parse_cpanfile(text: str, rel: str) -> list[Component]:
    return [Component("cpan", match.group(1), match.group(2) or "", rel)
            for match in re.finditer(r"(?m)^\s*(?:requires|recommends)\s+['\"]([^'\"]+)['\"](?:\s*,\s*['\"]([^'\"]+)['\"])?", text)]


def _parse_rockspec(text: str, rel: str) -> list[Component]:
    match = re.search(r"dependencies\s*=\s*\{(.*?)\}", text, re.DOTALL)
    if not match:
        return []
    components: list[Component] = []
    for item in re.findall(r"['\"]([^'\"]+)['\"]", match.group(1)):
        parts = item.split()
        if parts:
            components.append(Component("luarocks", parts[0], " ".join(parts[1:]), rel))
    return components


def _dedupe_components(components: list[Component]) -> list[Component]:
    seen: set[tuple[str, str, str, str]] = set()
    result: list[Component] = []
    for component in components:
        key = (component.ecosystem, component.name.lower(), component.constraint, component.source)
        if key in seen:
            continue
        seen.add(key)
        result.append(component)
    return result


# ---------------------------------------------------------------------------
# Public helpers shared with the Android engine and contract tests
# ---------------------------------------------------------------------------

def secret_findings(rel: str, text: str) -> list[Finding]:
    return _secret_findings(rel, text)


def secure_coding_findings(rel: str, text: str) -> list[Finding]:
    return _matches_checks(rel, text, CODE_CHECKS)


def gradle_dependencies(text: str, rel: str) -> list[Component]:
    return _parse_gradle(text, rel)


def c_like_balanced(text: str) -> bool:
    return _c_like_balanced(text)


# ---------------------------------------------------------------------------
# Registry helpers
# ---------------------------------------------------------------------------

def all_language_engines() -> list[LanguageEngine]:
    return [LanguageEngine(spec) for spec in SPECS]


def engine_for_key(key: str) -> LanguageEngine:
    return LanguageEngine(spec_by_key(key))
