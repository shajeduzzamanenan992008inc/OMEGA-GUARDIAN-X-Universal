# devkit - pillar 2: development ecosystem

| Folder | Purpose |
|---|---|
| `health/` | `run_project_checks.py` + `project-profile.json`: profile-driven CI checks (argument arrays, no shell) |
| `observability/` | central structured logging, error tracking, debug utilities (docs + demo) |
| `agents/` | AI-agent kit: canonical policy, 11 agents, 20 instructions, 6 prompts, 6 skills, Copilot instructions |
| `runbooks/` | incident, security-incident, release, migration, backup and AI-agent-safety runbooks |

Run the health checks of the host: `python omega-shield/devkit/health/run_project_checks.py`
(edit `health/project-profile.json` to declare your own build/test commands).
