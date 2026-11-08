from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any

from . import __version__
from urllib.parse import unquote

OSV_BATCH_URL = "https://api.osv.dev/v1/querybatch"
OSV_WEB = "https://osv.dev/vulnerability/"
ECOSYSTEMS = {
    "pypi": "PyPI", "npm": "npm", "cargo": "crates.io", "golang": "Go",
    "maven": "Maven", "composer": "Packagist", "gem": "RubyGems",
    "pub": "Pub", "nuget": "NuGet",
}


def _package_name(component: dict[str, Any]) -> tuple[str, str] | None:
    purl = component.get("purl", "")
    match = re.match(r"^pkg:([^/]+)/([^@?]+)", purl)
    if not match:
        return None
    package_type, name = match.groups()
    ecosystem = ECOSYSTEMS.get(package_type)
    if not ecosystem:
        return None
    return unquote(name), ecosystem


def _post_batch(queries: list[dict[str, Any]], timeout: int) -> list[dict[str, Any]]:
    request = urllib.request.Request(OSV_BATCH_URL, data=json.dumps({"queries": queries}).encode("utf-8"),
                                     headers={"Content-Type": "application/json", "Accept": "application/json",
                                              "User-Agent": f"OMEGA-GUARDIAN-X/{__version__}"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(8_000_000)
    except urllib.error.HTTPError as exc:
        detail = exc.read(1000).decode("utf-8", "replace")
        raise RuntimeError(f"OSV API returned HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"OSV API request failed: {type(exc).__name__}") from exc
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("OSV API returned invalid JSON.") from exc
    results = data.get("results")
    if not isinstance(results, list) or len(results) != len(queries):
        raise RuntimeError("OSV API response did not match the submitted query batch.")
    return results


def audit_components(components: list[dict[str, Any]], *, timeout: int = 30, max_packages: int = 1000) -> dict[str, Any]:
    """Query OSV for exact manifest-declared versions only; no source code is sent."""
    if not 1 <= timeout <= 120:
        raise ValueError("OSV timeout must be between 1 and 120 seconds.")
    if not 1 <= max_packages <= 1000:
        raise ValueError("OSV package cap must be between 1 and 1000.")
    eligible: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for component in components:
        version = str(component.get("version", "UNKNOWN"))
        package = _package_name(component)
        if version == "UNKNOWN" or not package:
            skipped.append({"name": str(component.get("name", "unknown")), "reason": "no exact supported version declaration"})
            continue
        name, ecosystem = package
        key = (ecosystem, name, version)
        if key in seen: continue
        seen.add(key)
        eligible.append({"package": {"name": name, "ecosystem": ecosystem}, "version": version,
                         "_display": {"name": name, "ecosystem": ecosystem, "version": version}})
    if len(eligible) > max_packages:
        skipped.extend({"name": item["_display"]["name"], "reason": "package audit cap exceeded"} for item in eligible[max_packages:])
        eligible = eligible[:max_packages]
    findings: list[dict[str, Any]] = []
    warnings: list[str] = []
    for offset in range(0, len(eligible), 100):
        batch = eligible[offset:offset + 100]
        active = [{"package": x["package"], "version": x["version"]} for x in batch]
        pages = 0
        for _ in range(10):
            pages += 1
            results = _post_batch(active, timeout)
            next_active = []
            for item, result in zip(batch, results):
                for vuln in result.get("vulns", []) if isinstance(result, dict) else []:
                    if not isinstance(vuln, dict) or not vuln.get("id"): continue
                    findings.append({"package": item["_display"], "id": vuln["id"],
                                     "modified": vuln.get("modified"), "url": OSV_WEB + str(vuln["id"]),
                                     "severity": "unknown", "status": "advisory-match; review affected ranges and applicability"})
                token = result.get("next_page_token") if isinstance(result, dict) else None
                if token:
                    next_active.append({"package": item["package"], "version": item["version"], "page_token": token})
                else:
                    item["_complete"] = True
            if not next_active:
                break
            # The next batch only includes queries that returned individual page tokens.
            batch = [item for item in batch if not item.get("_complete")]
            active = next_active
        else:
            warnings.append("OSV query pagination reached the 10-page safety cap; some advisory results may be incomplete.")
    return {"service": "OSV.dev", "endpoint": OSV_BATCH_URL, "packages_submitted": len(eligible),
            "advisories_found": len(findings), "findings": findings, "packages_skipped": skipped,
            "warnings": warnings,
            "privacy": "Only package ecosystem, package name and exact manifest-declared version were sent; source code and file contents were not transmitted.",
            "limitations": ["A match is an advisory candidate; review affected version ranges and applicability.",
                            "Manifest-declared versions may differ from installed/resolved versions.",
                            "Unresolved version ranges and unsupported ecosystems are skipped.",
                            "The OSV service is an external dependency and may be unavailable or change."]}
