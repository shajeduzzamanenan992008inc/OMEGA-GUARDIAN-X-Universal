# AI coding-agent support

OGX ships repository-level guidance for common coding agents:

| Agent / environment | Instruction file |
|---|---|
| GitHub Copilot | `.github/copilot-instructions.md` |
| Copilot custom agents | `.github/agents/*.agent.md` |
| OpenAI Codex and compatible agents | `AGENTS.md`, `CODEX.md` |
| Claude Code | `CLAUDE.md` |
| Gemini / Google AI Studio | `GEMINI.md` |
| Cursor | `.cursor/rules/ogx.mdc`, `CURSOR.md` |
| Windsurf | `WINDSURF.md` |

The OGX source repository has its own canonical instructions. For customer projects, `ogx integrate plan PATH` previews a project-root bundle and `ogx integrate apply PATH --confirm` creates only missing files. Existing agent instructions are preserved. The generated guides are governance prompts, not OS-enforced security controls; review agent changes and retain branch protection and CI policy.
