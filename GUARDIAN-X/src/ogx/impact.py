from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Any

from .architecture import analyze_architecture
from .config import ScanConfig, load_config


def analyze_impact(root: Path, changed_file: str, config: ScanConfig | None = None) -> dict[str, Any]:
    """Estimate import-based blast radius; does not claim runtime/data-flow impact."""
    root = root.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Project path must be a directory.")
    candidate = Path(changed_file)
    if candidate.is_symlink():
        raise ValueError("Changed file path must not be a symlink.")
    if candidate.is_absolute():
        try:
            relative = candidate.resolve(strict=False).relative_to(root).as_posix()
        except ValueError as exc:
            raise ValueError("Changed file must be inside the target project.") from exc
    else:
        relative = candidate.as_posix().replace("\\", "/")
    normalized = Path(relative)
    if normalized.is_absolute() or ".." in normalized.parts or not relative or relative.startswith("/"):
        raise ValueError("Changed file path must be a safe project-relative path.")
    target = root / normalized
    if target.is_symlink():
        raise ValueError("Changed file path must not be a symlink.")
    graph = analyze_architecture(root, config)
    nodes = set(graph["nodes"])
    if relative not in nodes:
        raise ValueError("Changed file is not in the currently supported static import graph. Try a source file supported by OGX.")
    reverse: dict[str, set[str]] = {}
    for edge in graph["edges"]:
        reverse.setdefault(edge["to"], set()).add(edge["from"])
    distance: dict[str, int] = {}
    queue: deque[str] = deque([relative])
    visited = {relative}
    while queue:
        node = queue.popleft()
        for dependent in sorted(reverse.get(node, set())):
            if dependent in visited:
                continue
            visited.add(dependent)
            distance[dependent] = distance.get(node, 0) + 1
            queue.append(dependent)
    impacted = [{"file": name, "distance": distance[name],
                 "relationship": "direct importer" if distance[name] == 1 else "transitive importer"}
                for name in sorted(distance, key=lambda item: (distance[item], item))]
    return {"project": root.name, "root": str(root), "changed_file": relative,
            "direct_dependents": [item["file"] for item in impacted if item["distance"] == 1],
            "transitive_dependents": [item["file"] for item in impacted if item["distance"] > 1],
            "impacted_files": impacted, "graph_nodes": len(nodes), "graph_edges": len(graph["edges"]),
            "confidence": "static-import-only",
            "limitations": ["Dynamic imports, reflection, generated code and runtime call paths can be missed.",
                            "This is not a complete change-impact or data-flow proof.",
                            "Non-Python/JavaScript languages are not semantically indexed by this graph yet."]}
