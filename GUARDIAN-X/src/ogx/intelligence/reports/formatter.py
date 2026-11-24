"""Terminal and Markdown intelligence reports.

Only repository-relative paths and declarative metadata are rendered.  Sensitive
file contents are never present in the intelligence model, so no redaction step
is required at presentation time.
"""

from __future__ import annotations

from typing import Any, Iterable

from ..models import ProjectIntelligence

DEFAULT_SECTIONS: tuple[str, ...] = (
    "overview", "languages", "frameworks", "technologies", "dependencies",
    "structure", "architecture", "entrypoints", "build", "testing",
    "documentation", "configuration", "health", "findings", "recommendations",
    "limitations",
)

SECTION_ALIASES = {
    "frameworks": ("frameworks", "technologies"),
    "entrypoints": ("entrypoints",),
    "build": ("build",),
    "testing": ("testing",),
    "documentation": ("documentation",),
    "configuration": ("configuration",),
    "health": ("health",),
}


def _data(report: ProjectIntelligence | dict[str, Any]) -> dict[str, Any]:
    return report.to_dict() if isinstance(report, ProjectIntelligence) else report


def _selected(sections: Iterable[str] | None) -> set[str]:
    if sections is None:
        return set(DEFAULT_SECTIONS)
    selected: set[str] = {"overview"}
    for section in sections:
        selected.update(SECTION_ALIASES.get(section, (section,)))
    return selected


def _md(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def _table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |",
             "|" + "|".join("---" for _ in headers) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(_md(value) for value in row) + " |")
    return lines


def _limits(entries: Iterable[Any], key: str = "detail", limit: int = 12) -> str:
    values: list[str] = []
    for item in list(entries)[:limit]:
        if isinstance(item, dict):
            value = item.get(key) or item.get("location") or item.get("item") or ""
            values.append(str(value))
        else:
            values.append(str(item))
    return "; ".join(values)


def render_terminal(report: ProjectIntelligence | dict[str, Any],
                    sections: Iterable[str] | None = None) -> str:
    data = _data(report)
    selected = _selected(sections)
    project = data["project"]
    metadata = data["metadata"]
    classification = data["classification"]
    lines: list[str] = [
        "OMEGA GUARDIAN X — Project Intelligence Report",
        "=" * 49,
        f"Project: {project['name']}",
        f"Root: {project['root']}",
        f"Project ID: {project['project_id']} (identity source: {project['identity']['source']})",
    ]
    if project["identity"].get("registry_project_id"):
        lines.append(f"Registry project: {project['identity']['registry_project_id']}")
    lines += [
        f"Classification: {classification['kind']} ({classification['confidence']})",
        f"Files indexed: {project['file_count']} ({project['total_bytes']} bytes); "
        f"mode: {metadata['analysis_mode']}",
        f"Generated: {metadata['generated_at']}; analysis: {metadata['analysis_id']}",
        f"Execution performed: {metadata['execution_performed']}; "
        f"network access: {metadata['network_access']}",
        "",
    ]

    if "languages" in selected and data["languages"]:
        lines += ["Languages", "---------"]
        for item in data["languages"]:
            lines.append(
                f"  {item['name']:<18} {item['role']:<18} files={item['file_count']:<5} "
                f"{item['percentage']:.1f}%  confidence={item['confidence']}")
            if item.get("evidence"):
                lines.append(f"    evidence: {_limits(item['evidence'], limit=3)}")
        lines.append("")

    if "frameworks" in selected:
        lines += ["Frameworks and technologies", "----------------------------"]
        if not data["frameworks"] and not data["technologies"]:
            lines.append("  No framework or technology indicator was established.")
        for item in data["frameworks"]:
            lines.append(f"  framework  {item['name']:<26} {item['confidence']:<10} "
                         f"{_limits(item['evidence'], limit=2)}")
        if "technologies" in selected:
            for item in data["technologies"]:
                lines.append(f"  technology {item['name']:<26} {item['category']:<15} "
                             f"{item['confidence']:<10} {_limits(item['evidence'], limit=2)}")
        lines.append("")

    if "dependencies" in selected:
        dependencies = data["dependencies"]
        lines += ["Dependencies", "------------",
                  f"  Declarations: {len(dependencies['declarations'])}; "
                  f"manifests: {len(dependencies['manifests'])}; "
                  f"lockfiles: {len(dependencies['lockfiles'])}",
                  f"  Resolved: {dependencies['resolved']}; transitive: {dependencies['transitive']}; "
                  f"network: {dependencies['network_access']}"]
        for manager in dependencies["package_managers"]:
            lines.append(f"  package manager: {manager['name']} ({manager['ecosystem']}) "
                         f"manifest={manager['manifest']} lockfile={manager['lockfile'] or 'none'}")
        for manifest in dependencies["manifests"]:
            lines.append(f"  manifest {manifest['status']:<18} {manifest['path']} "
                         f"({manifest['declaration_count']} declarations)")
        for declaration in dependencies["declarations"][:20]:
            lines.append(f"    {declaration['ecosystem']:<10} {declaration['scope']:<12} "
                         f"{declaration['name']} {declaration['constraint']} [{declaration['source']}]")
        if len(dependencies["declarations"]) > 20:
            lines.append(f"    ... {len(dependencies['declarations']) - 20} more declaration(s); use --format json")
        lines.append("")

    if "structure" in selected:
        structure = data["structure"]
        lines += ["Project structure", "-----------------",
                  f"  Files: {structure['file_count']}; directories: {structure['directory_count']}; "
                  f"generated files excluded: {structure['generated_files']}",
                  f"  Source roots: {', '.join(structure['source_directories']) or 'none'}",
                  f"  Test roots: {', '.join(structure['test_directories']) or 'none'}",
                  f"  Documentation roots: {', '.join(structure['documentation_directories']) or 'none'}",
                  f"  CI files: {', '.join(structure['ci_files']) or 'none'}"]
        if structure["monorepo_packages"]:
            lines.append("  Packages: " + ", ".join(item["path"] for item in structure["monorepo_packages"][:12]))
        if structure["workspace_indicators"]:
            lines.append("  Workspace indicators: " + _limits(structure["workspace_indicators"], limit=3))
        lines.append("")

    if "architecture" in selected:
        lines += ["Architecture indicators", "-----------------------"]
        if not data["architecture"]:
            lines.append("  No reliable architecture indicator was established.")
        for item in data["architecture"]:
            lines.append(f"  {item['name']:<24} {item['verdict']:<10} {item['summary']}")
        lines.append("")

    if "entrypoints" in selected and data["entry_points"]:
        lines += ["Entry-point candidates", "----------------------"]
        for item in data["entry_points"][:30]:
            lines.append(f"  {item['kind']:<26} {item['path']} ({item['confidence']})")
        lines.append("")

    if "build" in selected:
        lines += ["Build systems", "-------------"]
        if not data["build_systems"]:
            lines.append("  No supported build-system manifest was detected.")
        for item in data["build_systems"]:
            lines.append(f"  {item['name']:<28} {item['category']:<10} {item['manifest']} "
                         f"({item['confidence']})")
        lines.append("")

    if "testing" in selected:
        testing = data["testing"]
        lines += ["Testing and quality", "-------------------",
                  f"  Test files discovered: {testing['test_file_count']}; executed: {testing['executed']}",
                  f"  Frameworks: {', '.join(item['name'] for item in testing['frameworks']) or 'none'}",
                  f"  Config files: {', '.join(testing['config_files']) or 'none'}",
                  f"  Coverage configured: {testing['coverage_configured']}",
                  f"  Static analysis: {', '.join(testing['static_analysis']) or 'none'}",
                  f"  Type checking: {', '.join(testing['type_checking']) or 'none'}",
                  f"  CI test workflows: {', '.join(testing['ci_test_workflows']) or 'none'}",
                  ""]

    if "documentation" in selected:
        documentation = data["documentation"]
        lines += ["Documentation", "-------------"]
        for item in documentation["indicators"]:
            lines.append(f"  {'present' if item['present'] else 'missing':<8} {item['item']}"
                         + (f" — {item['evidence']}" if item["evidence"] else ""))
        lines.append("")

    if "configuration" in selected:
        configuration = data["configuration"]
        lines += ["Configuration", "-------------"]
        for category, paths in configuration["categories"].items():
            lines.append(f"  {category:<16} {len(paths)} file(s)")
        if configuration["sensitive_candidates"]:
            lines.append("  Possible sensitive files (contents not read):")
            for item in configuration["sensitive_candidates"][:20]:
                lines.append(f"    {item['path']} — {item['reason']}")
        lines.append("")

    if "health" in selected:
        health = data["health"]
        lines += ["Engineering health (indicator coverage, not a grade)",
                  "-----------------------------------------------",
                  f"  Score: {health['score'] if health['score'] is not None else 'unavailable'} "
                  f"({health['scored_dimensions']} scored, {health['unscored_dimensions']} not scored)"]
        for item in health["dimensions"]:
            score = "—" if item["score"] is None else f"{item['score']:.1f}"
            lines.append(f"  {item['name']:<26} {item['status']:<16} {score}  {item['rationale']}")
        lines += [f"  Method: {health['method']}", ""]

    if "findings" in selected:
        lines += ["Findings", "--------"]
        if not data["findings"]:
            lines.append("  No findings. This is not a security or quality clearance.")
        for item in data["findings"]:
            lines.append(f"  [{item['confidence']:<9}] {item['finding_id']}: {item['title']}")
            for evidence in item.get("evidence", [])[:2]:
                if isinstance(evidence, dict):
                    lines.append(f"      evidence: {evidence.get('location', '')} — "
                                 f"{evidence.get('detail', '')}")
                else:
                    lines.append(f"      evidence: {evidence}")
            for limitation in item.get("limitations", [])[:2]:
                lines.append(f"      limitation: {limitation}")
        lines.append("")

    if "recommendations" in selected and data["recommendations"]:
        lines += ["Recommendations", "---------------"]
        for item in data["recommendations"]:
            lines.append(f"  [{item['priority']:<6}] {item['action']} — {item['rationale']}")
        lines.append("")

    if "limitations" in selected:
        lines += ["Analysis limitations", "--------------------"]
        for item in data["limitations"]:
            lines.append(f"  {item['area']}: {item['detail']}")
        for warning in data.get("warnings", []):
            lines.append(f"  warning: {warning}")
        for error in data.get("errors", []):
            lines.append(f"  error: {error}")
        lines.append("")

    lines += [
        "Confidence reflects evidence strength (confirmed/likely/possible/unknown), not probability.",
        "Static analysis only: no project code, build, test, package manager or installer was executed.",
        "",
    ]
    return "\n".join(lines)


def render_markdown(report: ProjectIntelligence | dict[str, Any],
                    sections: Iterable[str] | None = None) -> str:
    data = _data(report)
    selected = _selected(sections)
    project = data["project"]
    metadata = data["metadata"]
    classification = data["classification"]
    lines: list[str] = [
        "# OMEGA GUARDIAN X — Project Intelligence Report", "",
        f"- **Project:** `{project['name']}`",
        f"- **Root:** `{project['root']}`",
        f"- **Project ID:** `{project['project_id']}` (identity source: `{project['identity']['source']}`)",
        f"- **Registry project:** `{project['identity'].get('registry_project_id') or 'none'}`",
        f"- **Classification:** `{classification['kind']}` ({classification['confidence']})",
        f"- **Files indexed:** {project['file_count']} ({project['total_bytes']} bytes)",
        f"- **Mode:** `{metadata['analysis_mode']}`; execution performed: "
        f"`{metadata['execution_performed']}`; network access: `{metadata['network_access']}`",
        f"- **Generated:** {metadata['generated_at']}",
        f"- **Analysis ID:** `{metadata['analysis_id']}`", "",
    ]

    if "languages" in selected:
        lines += ["## Languages", ""]
        if data["languages"]:
            lines += _table(["Language", "Role", "Files", "Share", "Confidence"],
                            [[item["name"], item["role"], item["file_count"],
                              f"{item['percentage']:.1f}%", item["confidence"]]
                             for item in data["languages"]])
        else:
            lines.append("No supported language indicator was detected.")
        lines.append("")

    if "frameworks" in selected:
        lines += ["## Frameworks and technologies", ""]
        rows = [[item["name"], item["category"], item["ecosystem"], item["confidence"],
                 _limits(item["evidence"], limit=2)]
                for item in data["technologies"]]
        rows += [[item["name"], "framework", item["ecosystem"], item["confidence"],
                  _limits(item["evidence"], limit=2)]
                 for item in data["frameworks"]]
        if rows:
            lines += _table(["Name", "Category", "Ecosystem", "Confidence", "Evidence"], rows)
        else:
            lines.append("No framework or technology indicator was established.")
        lines.append("")

    if "dependencies" in selected:
        dependencies = data["dependencies"]
        lines += ["## Dependencies", "",
                  f"- Declarations: **{len(dependencies['declarations'])}**",
                  f"- Manifests: **{len(dependencies['manifests'])}**; lockfiles: "
                  f"**{len(dependencies['lockfiles'])}**",
                  f"- Resolved: `{dependencies['resolved']}`; transitive: "
                  f"`{dependencies['transitive']}`; network: `{dependencies['network_access']}`", ""]
        if dependencies["package_managers"]:
            lines += _table(["Package manager", "Ecosystem", "Manifest", "Lockfile"],
                            [[item["name"], item["ecosystem"], item["manifest"],
                              item["lockfile"] or "none"] for item in dependencies["package_managers"]])
            lines.append("")
        if dependencies["declarations"]:
            lines += ["### Declared dependencies", ""]
            lines += _table(["Ecosystem", "Scope", "Name", "Constraint", "Source"],
                            [[item["ecosystem"], item["scope"], item["name"],
                              item["constraint"] or "(none)", item["source"]]
                             for item in dependencies["declarations"][:100]])
            if len(dependencies["declarations"]) > 100:
                lines.append(f"\n> Showing 100 of {len(dependencies['declarations'])} declarations; "
                             "use the JSON report for the complete inventory.")
            lines.append("")

    if "structure" in selected:
        structure = data["structure"]
        lines += ["## Project structure", "",
                  f"- Files: **{structure['file_count']}**; directories: "
                  f"**{structure['directory_count']}**; generated files excluded: "
                  f"**{structure['generated_files']}**",
                  f"- Source roots: {', '.join(f'`{item}`' for item in structure['source_directories']) or 'none'}",
                  f"- Test roots: {', '.join(f'`{item}`' for item in structure['test_directories']) or 'none'}",
                  f"- Documentation roots: {', '.join(f'`{item}`' for item in structure['documentation_directories']) or 'none'}",
                  f"- CI files: {', '.join(f'`{item}`' for item in structure['ci_files']) or 'none'}", ""]
        if structure["monorepo_packages"]:
            lines += ["### Package roots", ""]
            lines += _table(["Path", "Manifests", "Language hint"],
                            [[item["path"], ", ".join(item["manifests"]), item["language_hint"] or "unknown"]
                             for item in structure["monorepo_packages"][:50]])
            lines.append("")
        if structure["tree"]:
            lines += ["### Bounded tree", "", "```"]
            for item in structure["tree"][:80]:
                indent = "  " * max(item["path"].count("/"), 0)
                lines.append(f"{indent}{item['path'].rsplit('/', 1)[-1]}"
                             + ("/" if item["kind"] == "directory" else ""))
            lines += ["```", ""]

    if "architecture" in selected:
        lines += ["## Architecture indicators", ""]
        if data["architecture"]:
            lines += _table(["Indicator", "Verdict", "Summary", "Evidence"],
                            [[item["name"], item["verdict"], item["summary"],
                              _limits(item["evidence"], limit=3)] for item in data["architecture"]])
        else:
            lines.append("No reliable architecture indicator was established.")
        lines.append("")

    if "entrypoints" in selected:
        lines += ["## Entry-point candidates", ""]
        if data["entry_points"]:
            lines += _table(["Kind", "Path", "Confidence", "Evidence"],
                            [[item["kind"], item["path"], item["confidence"], item["evidence"]]
                             for item in data["entry_points"][:100]])
        else:
            lines.append("No conventional entry point was detected.")
        lines.append("")

    if "build" in selected:
        lines += ["## Build systems", ""]
        if data["build_systems"]:
            lines += _table(["Build system", "Category", "Manifest", "Confidence", "CI workflows"],
                            [[item["name"], item["category"], item["manifest"], item["confidence"],
                              ", ".join(item["ci_workflows"]) or "none"]
                             for item in data["build_systems"]])
        else:
            lines.append("No supported build-system manifest was detected.")
        lines.append("")

    if "testing" in selected:
        testing = data["testing"]
        lines += ["## Testing and quality", "",
                  f"- Test files discovered: **{testing['test_file_count']}** (executed: "
                  f"`{testing['executed']}`)",
                  f"- Frameworks: {', '.join(item['name'] for item in testing['frameworks']) or 'none'}",
                  f"- Test configuration: {', '.join(f'`{item}`' for item in testing['config_files']) or 'none'}",
                  f"- Coverage configured: `{testing['coverage_configured']}`",
                  f"- Static analysis: {', '.join(f'`{item}`' for item in testing['static_analysis']) or 'none'}",
                  f"- Type checking: {', '.join(f'`{item}`' for item in testing['type_checking']) or 'none'}",
                  f"- CI test workflows: {', '.join(f'`{item}`' for item in testing['ci_test_workflows']) or 'none'}",
                  ""]

    if "documentation" in selected:
        documentation = data["documentation"]
        lines += ["## Documentation", ""]
        lines += _table(["Indicator", "Present", "Evidence"],
                        [[item["item"], "yes" if item["present"] else "no", item["evidence"] or "—"]
                         for item in documentation["indicators"]])
        lines.append("")

    if "configuration" in selected:
        configuration = data["configuration"]
        lines += ["## Configuration", ""]
        if configuration["categories"]:
            lines += _table(["Category", "Files"],
                            [[category, len(paths)] for category, paths in configuration["categories"].items()])
        else:
            lines.append("No recognized configuration category was detected.")
        if configuration["sensitive_candidates"]:
            lines += ["", "### Possible sensitive files (contents not read)", ""]
            lines += _table(["Path", "Reason"],
                            [[item["path"], item["reason"]]
                             for item in configuration["sensitive_candidates"][:50]])
        lines.append("")

    if "health" in selected:
        health = data["health"]
        lines += ["## Engineering health", "",
                  f"**Indicator coverage:** {health['score'] if health['score'] is not None else 'unavailable'} "
                  f"({health['scored_dimensions']} scored, {health['unscored_dimensions']} excluded)", "",
                  f"> {health['method']}", ""]
        lines += _table(["Dimension", "Status", "Score", "Rationale"],
                        [[item["name"], item["status"],
                          "—" if item["score"] is None else f"{item['score']:.1f}", item["rationale"]]
                         for item in health["dimensions"]])
        lines.append("")

    if "findings" in selected:
        lines += ["## Findings", ""]
        if data["findings"]:
            lines += _table(["Confidence", "Rule", "Title", "Limitations"],
                            [[item["confidence"], item["finding_id"], item["title"],
                              _limits(item.get("limitations", []), limit=2)]
                             for item in data["findings"]])
        else:
            lines.append("No findings were produced. This is not a security or quality clearance.")
        lines.append("")

    if "recommendations" in selected:
        lines += ["## Recommendations", ""]
        if data["recommendations"]:
            lines += _table(["Priority", "Recommendation", "Rationale"],
                            [[item["priority"], item["action"], item["rationale"]]
                             for item in data["recommendations"]])
        else:
            lines.append("No evidence-based recommendation was produced.")
        lines.append("")

    if "limitations" in selected:
        lines += ["## Limitations", ""]
        for item in data["limitations"]:
            lines.append(f"- **{item['area']}:** {item['detail']}")
        for warning in data.get("warnings", []):
            lines.append(f"- WARNING: {warning}")
        for error in data.get("errors", []):
            lines.append(f"- ERROR: {error}")
        lines.append("")

    lines += ["> Static, read-only analysis. No project code, build, test, package manager, "
              "installer or network request was executed. Confidence reflects evidence strength, "
              "not probability.", ""]
    return "\n".join(lines)
