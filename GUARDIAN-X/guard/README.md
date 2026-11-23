# guard - pillar 1: protection

| Folder | Purpose |
|---|---|
| `gates/` | commit/push/CI gates (`scan --strict`): installed with `init.py hooks install --confirm`, or call `ci-gate.sh` |
| `policies/` | restrict-only project policy (the active file is `../shield.toml`) |
| `runtime/` | request guard configuration + runnable examples (firewall, validation, rate limit, access control) |

The code for all of this lives in `core/` and is vendored; **edit configuration here, never inside `core/`**.
