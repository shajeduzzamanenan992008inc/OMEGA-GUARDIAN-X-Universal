from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

from .config import ScanConfig, load_config
from .inventory import BINARY_SUFFIXES

SOURCE_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}


def analyze_architecture(root: Path, config: ScanConfig | None = None, max_nodes: int = 5000) -> dict[str, Any]:
    """Build a conservative static import graph; never executes target code."""
    root = root.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"Project path is not a directory: {root}")
    config = config or load_config()
    excluded = set(config.excludes)
    sources: dict[str, tuple[str, str]] = {}
    warnings: list[str] = []
    considered = 0
    for current, dirs, names in __import__("os").walk(root, topdown=True, followlinks=False):
        base = Path(current)
        dirs[:] = [d for d in dirs if d not in excluded and not (base / d).is_symlink()]
        for name in names:
            path = base / name
            if path.is_symlink() or not path.is_file() or path.suffix.lower() not in SOURCE_SUFFIXES:
                continue
            considered += 1
            if len(sources) >= max_nodes:
                warnings.append(f"Architecture graph limited to {max_nodes} source files.")
                break
            try:
                if path.stat().st_size > config.max_file_bytes:
                    warnings.append(f"Skipped oversized source file: {path.relative_to(root).as_posix()}")
                    continue
                sources[path.relative_to(root).as_posix()] = (path.suffix.lower(), path.read_text(encoding="utf-8-sig"))
            except (OSError, UnicodeError) as exc:
                warnings.append(f"Could not read {path.relative_to(root).as_posix()}: {type(exc).__name__}")
        if len(sources) >= max_nodes:
            break

    nodes = sorted(sources)
    edges: set[tuple[str, str]] = set()
    symbols: list[dict[str, Any]] = []
    known = set(nodes)
    for rel, (suffix, source) in sources.items():
        if suffix == ".py":
            try:
                tree = ast.parse(source, filename=rel)
            except SyntaxError as exc:
                warnings.append(f"Python parse failed for {rel}:{exc.lineno}: {exc.msg}")
                continue
            for item in ast.walk(tree):
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    symbols.append({"file": rel, "kind": "class" if isinstance(item, ast.ClassDef) else "function",
                                    "name": item.name, "line": item.lineno})
                elif isinstance(item, ast.Import):
                    for alias in item.names:
                        target = alias.name.replace(".", "/")
                        for candidate in known:
                            stem = candidate.removesuffix(".py")
                            if stem == target or stem.endswith("/" + target):
                                edges.add((rel, candidate))
                elif isinstance(item, ast.ImportFrom) and item.module:
                    target = item.module.replace(".", "/")
                    for candidate in known:
                        stem = candidate.removesuffix(".py")
                        if stem == target or stem.endswith("/" + target):
                            edges.add((rel, candidate))
        else:
            pattern = re.compile(r"(?:from\s+['\"]([^'\"]+)['\"]\s+import|import\s+(?:[^'\"]*?from\s+)?['\"]([^'\"]+)['\"]|require\(\s*['\"]([^'\"]+)['\"]\s*\))")
            for match in pattern.finditer(source):
                module = next((x for x in match.groups() if x), "")
                if not module.startswith("."):
                    continue
                base_rel = (Path(rel).parent / module).as_posix()
                stems = {base_rel, base_rel + ".js", base_rel + ".jsx", base_rel + ".ts", base_rel + ".tsx",
                         base_rel + ".mjs", base_rel + ".cjs", base_rel.rstrip("/") + "/index.js",
                         base_rel.rstrip("/") + "/index.ts"}
                for candidate in known & stems:
                    edges.add((rel, candidate))

    return {"root": str(root), "nodes": nodes,
            "edges": [{"from": a, "to": b} for a, b in sorted(edges)],
            "symbols": sorted(symbols, key=lambda x: (x["file"], x["line"])),
            "source_files_considered": considered, "warnings": warnings,
            "limitations": ["Static import graph only; dynamic imports, reflection and runtime call paths may be missed.",
                            "No language server, whole-program semantic index or data-flow engine is used.",
                            "An edge means a static import/reference was matched, not proof of runtime execution."]}


def architecture_mermaid(data: dict[str, Any], max_edges: int = 250) -> str:
    lines = ["flowchart LR"]
    for i, node in enumerate(data.get("nodes", [])):
        lines.append(f'  N{i}["{node.replace(chr(34), chr(39))}"]')
    index = {node: f"N{i}" for i, node in enumerate(data.get("nodes", []))}
    for edge in data.get("edges", [])[:max_edges]:
        if edge["from"] in index and edge["to"] in index:
            lines.append(f"  {index[edge['from']]} --> {index[edge['to']]}")
    return "\n".join(lines) + "\n"
