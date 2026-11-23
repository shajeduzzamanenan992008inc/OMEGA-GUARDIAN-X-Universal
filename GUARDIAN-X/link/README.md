# link - pillar 3: opt-in connection to host tools

Nothing is linked by default. `init.py link plan` shows the available groups; `link apply --with <groups> --confirm`
creates only files that do not exist yet (never overwrites), records SHA-256 ownership in `../link-manifest.json`,
and `link remove --confirm` deletes only files that are still unchanged.

| Group | Creates |
|---|---|
| `copilot` | `.github/copilot-instructions.md`, `.github/instructions/`, `.github/agents/` |
| `agents` | `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `CODEX.md`, `CURSOR.md`, `WINDSURF.md` |
| `editors` | rule files for Cursor, Cline, Windsurf, Continue, Roo |
| `deps` | `.github/dependabot.yml`, `renovate.json` |

`templates/` holds hand-copyable examples (CI workflows, community files, Dependabot) that `link` does not install.
