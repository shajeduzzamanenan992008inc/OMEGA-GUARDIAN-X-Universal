# OMEGA Shield (OGX) - universal plug-and-play shield & dev core

Drop **one folder** into the root of any project. **One entry point** (`init.py`). **Python 3.11+, standard library only** - no packages to install.

```
python omega-shield/init.py status
```

| Pillar | Folder | You get |
|---|---|---|
| **Protect** | `guard/` | commit/push/CI gate, read-only security scanner, **runtime request guard** (hygiene, attack signatures, rate limiting, IP policy, API-key/role access control) as WSGI/ASGI middleware or a reverse proxy for apps in any language |
| **Develop** | `devkit/` | profile-driven health checks, **central structured logging**, **error tracking**, debug utilities, AI-agent kit (11 agents, 20 instructions, 6 prompts, 6 skills), runbooks |
| **Connect** | `link/` | *opt-in* files for Copilot, agent guides, editor rules, Dependabot/Renovate - nothing is created unless you choose it |

## Quick start
```
python omega-shield/init.py scan --strict            # read-only scan; exit 1 on high/critical
python omega-shield/init.py audit                    # health + security + maintenance report
python omega-shield/init.py hooks install --confirm  # git pre-commit gate
python omega-shield/init.py guard test "/s?q=<script>alert(1)</script>"      # try the request guard (exit 1 = blocked)
python omega-shield/init.py proxy --upstream http://127.0.0.1:3000           # guard any app (experimental)
python omega-shield/init.py logs                     # central log events
python omega-shield/init.py link plan                # what could be linked (changes nothing)
```
Embed in a Python app without touching `sys.path` permanently:
```python
import runpy
shield = runpy.run_path("omega-shield/init.py")["load_runtime"]()
app = shield.ShieldMiddleware(app)       # WSGI;  shield.ShieldASGI(app) for ASGI
```

## Zero-conflict contract
1. Location is found from `__file__` - cwd, `PYTHONPATH` and the host layout do not matter.
2. Nothing is written inside the host unless a command says `--confirm` (git hooks, `link`).
3. No `.pyc`, no permanent `sys.path` change; only the `omega.*` loggers are configured.
4. Reports, logs and error data live **outside** the project in a per-host state folder (`init.py status` shows it).
5. The shield folder is excluded from its own scans; names contain no spaces.

## Layout
```
omega-shield/
├─ init.py  shield.toml  VERSION  README(.bn).md  LICENSE  SECURITY.md  CHANGELOG.md  AGENTS.md ...
├─ core/ogx/   kernel · connectors · analysis · shield · devkit · governance · ecosystem · runtime · cli   (vendored, stdlib-only)
├─ guard/      gates/  policies/  runtime/
├─ devkit/     health/  observability/  agents/  runbooks/
├─ link/       templates/
├─ docs/  schemas/  scripts/
└─ _dev/  .github/     tests, tools, release material, this repo's CI (not in the drop-in)
```
Details: `docs/ARCHITECTURE.md` - integration: `docs/INTEGRATION.md` - runtime guard: `docs/RUNTIME_GUARD.md` - migration from 0.7: `docs/MIGRATION.md`.

## Honest limits
* The scanner is heuristic (static rules, no data-flow analysis, no DAST). A clean scan is not proof of safety.
* The runtime guard is defence in depth, **not a managed WAF**; multipart bodies are not inspected; the proxy is experimental (HTTP/1.1, no TLS/WebSocket/chunked requests).
* Governance RBAC identities are not authenticated (local prototype).
* Verified on Linux with Python 3.12. Windows and Python 3.11 are expected to work (no OS-specific code was added) but were not exercised here.

Develop & test: `PYTHONPATH=core python -m unittest discover -s _dev/tests` - build the lean drop-in zip: `python _dev/tools/make_dist.py`.
