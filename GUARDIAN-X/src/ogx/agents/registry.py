from __future__ import annotations

import shutil
from typing import Any

AGENTS = {
    "copilot": {"display": "GitHub Copilot CLI / VS Code", "commands": ["copilot"], "interfaces": ["cli", "vscode", "mcp"]},
    "codex": {"display": "OpenAI Codex-compatible agent", "commands": ["codex"], "interfaces": ["cli", "mcp"]},
    "claude": {"display": "Claude Code", "commands": ["claude"], "interfaces": ["cli", "mcp"]},
    "gemini": {"display": "Gemini CLI", "commands": ["gemini"], "interfaces": ["cli", "mcp"]},
    "cursor": {"display": "Cursor", "commands": ["cursor"], "interfaces": ["ide", "cli"]},
    "windsurf": {"display": "Windsurf", "commands": ["windsurf"], "interfaces": ["ide"]},
    "aider": {"display": "Aider", "commands": ["aider"], "interfaces": ["cli"]},
    "cline": {"display": "Cline", "commands": [], "interfaces": ["vscode-extension"]},
    "continue": {"display": "Continue", "commands": [], "interfaces": ["ide-extension"]},
    "roo-code": {"display": "Roo Code", "commands": [], "interfaces": ["vscode-extension"]},
    "mcp": {"display": "Custom MCP server", "commands": [], "interfaces": ["mcp"]},
    "local-llm": {"display": "Local LLM adapter", "commands": ["ollama", "llama-cli"], "interfaces": ["http", "cli"]},
}


def discover_agents() -> list[dict[str, Any]]:
    result = []
    for key, item in AGENTS.items():
        found = [name for name in item["commands"] if shutil.which(name)]
        result.append({"id": key, **item, "detected_commands": found,
                       "status": "detected" if found else "adapter-defined; runtime not detected"})
    return result
