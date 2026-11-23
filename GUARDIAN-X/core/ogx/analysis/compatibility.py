from __future__ import annotations

from pathlib import Path
from typing import Any

from ogx.analysis.detector import detect_project
from ogx.analysis.toolchains import discover_toolchains

TOOL_MAP = {
    "Python": ["python", "pip"], "JavaScript/TypeScript": ["node", "npm"], "Rust": ["cargo", "rustc"],
    "Go": ["go"], "Java": ["java", "mvn"], "Java/JVM": ["java", "gradle"],
    "Kotlin": ["java", "gradle"], "Kotlin/JVM": ["java", "gradle"], "PHP": ["php", "composer"],
    "Ruby": ["ruby", "bundle"], "Swift": ["swift"], "C/C++": ["cmake"], ".NET": ["dotnet"],
    "C#": ["dotnet"], "Dart": ["dart"], "Scala": ["java", "gradle"], "Elixir": ["elixir", "mix"],
    "Erlang": ["erl"], "Lua": ["lua"], "Perl": ["perl"], "R": ["Rscript"], "Julia": ["julia"],
    "Haskell": ["ghc", "cabal"], "Solidity": ["solc"], "Crystal": ["crystal"], "SQL": [],
    "Terraform": ["terraform"], "Protocol Buffers": ["protoc"], "Clojure": ["clojure"],
}


def compatibility_report(root: Path) -> dict[str, Any]:
    profile = detect_project(root)
    tools = {x["name"]: x["available"] for x in discover_toolchains()}
    languages = []
    for language in profile.languages:
        required = TOOL_MAP.get(language, [])
        languages.append({"language": language, "detection": "detected",
                          "static_scan": "generic heuristics available; language-specific semantic analysis varies",
                          "toolchains": [{"name": t, "available": tools.get(t, False)} for t in required],
                          "native_build_test": "not executed by compatibility inspection"})
    return {"project": profile.name, "root": profile.root, "frameworks": profile.frameworks,
            "languages": languages,
            "overall": "partial-capability" if profile.languages else "generic-only",
            "warning": "Detection is not certification. Tool availability does not prove full language/framework support."}
