# OMEGA Shield (OGX) - universal plug-and-play shield & dev core

**Version:** 0.8.0a1. The canonical installable package lives in `src/ogx`; `core/ogx` is the hand-maintained drop-in loaded by `init.py`.

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
├─ core/ogx/   kernel · connectors · analysis · shield · devkit · governance · ecosystem · runtime · cli   (hand-maintained drop-in, stdlib-only; not generated from src/ogx)
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
* Locally verified for this working tree on Windows with CPython 3.15.2: 465 canonical-source tests passed (15 platform-gated skips) and 245 drop-in tests passed (9 platform-gated skips), plus a clean wheel install and CLI smoke checks. The earlier 340/11 completion-pass figure is retained only as history. Host lifecycle is implemented for two read-only local hosts; remote, SSH, Docker and Kubernetes hosts are not. Python 3.11/3.12/3.13 and Linux were not available locally; the CI matrix is configured but has not been executed here.

Develop & test (canonical source): `python -m pytest -q` - drop-in and architecture tests: `PYTHONPATH=core python -m unittest discover -s _dev/tests` - version/structure check: `python _dev/tools/sync_core_from_src.py --check` - build the lean drop-in zip: `python _dev/tools/make_dist.py`.

CI lives only in the repository root `.github/workflows/` (the workflow uses `working-directory: GUARDIAN-X`). The nested `GUARDIAN-X/.github/` directory is reusable kit content and contains no active workflows.

## Universal host lifecycle & engineering (Milestone H — in progress)

`src/ogx/hosts/` adds a synchronous host **session lifecycle** (`HostSessionManager`)
and two **operational local hosts**: a scoped read-only **local filesystem host**
and a **local Git host**. Both are read-only; they do not perform writes, remote
operations, push/branch operations, or execution. Health/recovery, CLI host
commands, and documentation are implemented.

```bash
# register, connect, health, recover, sessions, inspect — local hosts only so far
git clone https://github.com/acme/demo local-git && cd local-git
ogx host register local-git <id> --kind git   # registers, starts at registered
ogx host connect <id>                          # process-local connect, guarded adapter
ogx host health <id>                           # computed from known router directories
ogx host recover <id>                          # best-effort reconcile of a live session
git clone /path/to/project local-fs && cd local-fs
ogx host register local-fs <id> --kind filesystem --root /path/to/project
ogx host connect <id>
ogx host sessions                               # list live (non-terminal) sessions
ogx host status                                 # enhanced status w/ live state
git status                                      # host-shield harness below
```

Capability matrix was rewritten to **truthful read-only values**; registration
starts hosts at `registered` (not `active`); new statuses
`REGISTERED`/`DISCONNECTED`/`FAILED` were added to `src/ogx/hosts/models.py`. The
local hosts are `OPERATIONAL` for their implemented read-only surface (list, read,
metadata, file, tree, branches, tags, commits, history, discover, connect,
disconnect, health, recover, shutdown, dispose, lookup).

Truths that hold despite the `OPERATIONAL` label: these local hosts are read-only;
`effective_capabilities()` returns the adapter's reported values only while
connected/degraded, otherwise every declared capability is `unavailable`; and
registration does **not** mark a host `active`.

Local verification: 68 host-lifecycle/adapter/CLI/contract tests passed (3 skips),
within a full canonical suite of 465 passed / 15 skipped, and a 245-test drop-in
suite, and a clean wheel install plus drop-in smoke.

Docs: `docs/HOST_SESSION_LIFECYCLE.md`, `docs/LOCAL_FILESYSTEM_HOST.md`,
`docs/LOCAL_GIT_HOST.md`, `docs/HOST_HEALTH_AND_RECOVERY.md`,
`docs/HOST_LIFECYCLE_CI_VALIDATION.md`, `docs/HOST_LIFECYCLE_VERIFICATION_REPORT.md`,
`docs/HOST_ARCHITECTURE.md`, `docs/PLATFORM_SUPPORT_MATRIX.md`.

**Only local hosts are implemented.** Remote SSH, hosted Git hosts (GitHub/GitLab/
Bitbucket/etc.), Docker and Kubernetes hosts are **not** implemented — only a short
roadmap is recorded.

## Universal provider engine (Milestone G)

`src/ogx/platform/` is a provider-neutral engine for Git hosting, workspaces,
CI/CD, project management, package registries and cloud platforms. It is
standard-library only and reuses the existing registry, state directory and
approval workflow.

```bash
ogx providers list                      # every registered provider and its state
ogx providers inspect github            # declared capabilities and credential source
ogx providers capabilities github       # per-capability states
ogx platform status                     # engine-level summary
ogx platform capabilities               # machine-readable capability matrix
ogx host discover --provider github     # read-only repository discovery
ogx remote inspect https://github.com/acme/demo --provider github
ogx remote activate https://github.com/acme/demo --provider github
```

Capability states are `supported`, `partial`, `unsupported`, `requires_auth`,
`requires_permission`, `requires_workspace`, `not_configured`, `unavailable`
and `failed`. A capability is advertised as `supported` only when the adapter
actually implements it; a missing capability is never reported as success.

Writes are limited to approval-gated pull/merge request creation
(`ogx platform pr ... --confirm`) and are recorded in
`<state_dir>/platform/audit.jsonl`. Deleting, merging, pushing to protected
branches, changing permissions and deploying are not implemented.

The registry exposes 28 descriptors: 7 verified Git-hosting adapters (GitHub,
GitLab, Bitbucket, Gitea, Forgejo, Azure DevOps Repos, AWS CodeCommit) and 21
descriptor-only entries for CI/CD, project management, package registries and
cloud/workspace providers. Descriptor-only entries advertise only
identification, auth-discovery and API-version metadata; they are not
operational integrations.

Details: `docs/PROVIDER_ARCHITECTURE.md`, `docs/PLATFORM_SUPPORT_MATRIX.md`,
`docs/AUTHENTICATION.md`.
