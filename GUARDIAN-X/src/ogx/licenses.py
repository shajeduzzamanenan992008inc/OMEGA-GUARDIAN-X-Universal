from __future__ import annotations

import re
from pathlib import Path
from typing import Any

LICENSE_NAMES = {"LICENSE", "LICENCE", "COPYING", "NOTICE", "COPYRIGHT"}
SPDX_RE = re.compile(r"(?im)^\s*(?:SPDX-License-Identifier|license)\s*:\s*([^\r\n]+)")


def inventory_licenses(root: Path) -> dict[str, Any]:
    root = root.expanduser().resolve(strict=True)
    found: list[dict[str, str]] = []
    for current, dirs, files in __import__("os").walk(root, topdown=True, followlinks=False):
        base = Path(current)
        dirs[:] = [d for d in dirs if d not in {".git", "node_modules", ".venv", "venv", "vendor", "dist", "build", "target"} and not (base / d).is_symlink()]
        for name in files:
            path = base / name
            if path.is_symlink() or not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            try:
                if path.stat().st_size > 200_000:
                    continue
                if name.upper() in LICENSE_NAMES or name.upper().startswith(("LICENSE.", "LICENCE.", "COPYING.")):
                    text = path.read_text(encoding="utf-8", errors="replace")[:12000]
                    match = SPDX_RE.search(text)
                    found.append({"path": rel, "spdx_identifier": match.group(1).strip() if match else "UNDETERMINED"})
                elif path.suffix.lower() in {".py", ".js", ".ts", ".go", ".rs", ".java", ".php", ".rb", ".cs", ".cpp", ".h"}:
                    with path.open("r", encoding="utf-8", errors="replace") as stream:
                        head = "".join(stream.readline() for _ in range(12))
                    match = SPDX_RE.search(head)
                    if match:
                        found.append({"path": rel, "spdx_identifier": match.group(1).strip()})
            except OSError:
                continue
    return {"root": str(root), "license_files": found,
            "limitations": ["This is a license-file and SPDX-header inventory, not legal advice.",
                            "It does not resolve transitive dependency licenses or determine redistribution obligations."]}
