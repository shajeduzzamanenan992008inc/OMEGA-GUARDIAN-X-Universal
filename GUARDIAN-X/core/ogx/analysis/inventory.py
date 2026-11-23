from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from ogx.kernel.config import ScanConfig, load_config

BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".7z", ".gz", ".tar", ".whl", ".exe", ".dll", ".so", ".dylib", ".bin", ".db", ".sqlite", ".mp4", ".mp3", ".woff", ".woff2", ".ttf", ".otf"}


def build_inventory(root: Path, config: ScanConfig | None = None) -> dict[str, Any]:
    root = root.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"Project path is not a directory: {root}")
    config = config or load_config()
    excluded = set(config.excludes)
    counts: Counter[str] = Counter()
    top_dirs: Counter[str] = Counter()
    files: list[dict[str, Any]] = []
    total_bytes = 0
    skipped = 0
    warnings: list[str] = []
    for current, dirs, names in __import__("os").walk(root, topdown=True, followlinks=False):
        base = Path(current)
        dirs[:] = [d for d in dirs if d not in excluded and not (base / d).is_symlink()]
        for name in names:
            path = base / name
            if path.is_symlink() or not path.is_file():
                skipped += 1
                continue
            rel = path.relative_to(root).as_posix()
            if len(files) >= config.max_files:
                warnings.append(f"Inventory truncated at max_files={config.max_files}.")
                return {"root": str(root), "files": files, "file_count": len(files), "skipped": skipped,
                        "total_bytes": total_bytes, "extensions": dict(counts), "top_level_directories": dict(top_dirs), "warnings": warnings}
            try:
                size = path.stat().st_size
            except OSError:
                skipped += 1
                continue
            suffix = path.suffix.lower() or "[no-extension]"
            counts[suffix] += 1
            first = rel.split("/", 1)[0]
            if "/" in rel:
                top_dirs[first] += 1
            total_bytes += size
            files.append({"path": rel, "size_bytes": size, "extension": suffix,
                          "kind": "binary" if suffix in BINARY_SUFFIXES else "candidate-text"})
    return {"root": str(root), "files": files, "file_count": len(files), "skipped": skipped,
            "total_bytes": total_bytes, "extensions": dict(counts.most_common()),
            "top_level_directories": dict(top_dirs.most_common()), "warnings": warnings}
