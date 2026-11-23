# Migration from 0.7.x

| 0.7 | 0.8 |
|---|---|
| `GUARDIAN-X/core/ogx/<module>.py` (flat) | `core/ogx/<layer>/<module>.py` (see `ARCHITECTURE.md`) |
| `ogx.enterprise.*` | `ogx.governance.*` (`governance.py` -> `framework.py`) |
| `ogx.agents.registry`, `ogx.skills.registry` | `ogx.ecosystem.agents`, `ogx.ecosystem.skills` |
| `ogx.integration` | `ogx.ecosystem.integration` (now an opt-in linker) |
| `cli.py` (1,391 lines) | `ogx/cli/{app,common}.py` + `ogx/cli/commands/*.py` |
| `OMEGA GUARDIAN X/` control folder (spaces) in every host | the shield folder `omega-shield/` itself; no control folder |
| `omega-shield/shield.toml` | `omega-shield/shield.toml` |
| `integrate apply` created up to 30 files in 14 places | `link apply --with <groups> --confirm` creates only the groups you choose; nothing by default |
| `omega-shield/link-manifest.json` | `omega-shield/link-manifest.json` |
| three different exclusion lists | `ogx.kernel.paths.default_excludes()` (shield folder always excluded) |
| versions `0.4.0a1`/`0.5.0a1`/`0.7.0a1`/`0.7.1a1` | single `VERSION` file (a test keeps the fallback constant equal) |
| `tests/` | `_dev/tests/` |
| `.github/{agents,instructions,prompts,skills,runbooks,scripts,templates}` | `devkit/agents/*`, `devkit/runbooks`, `devkit/health`, `link/templates` |
| `.github/workflows` (this repo's CI) | unchanged location (`.github/` at the repo root), commands updated |
| `GUARDIAN-X/{OGX-*.ps1, scripts/*.ps1}`, `build/`, `dist/`, `release/*.whl` | removed (duplicates / build outputs); `scripts/scan_many.py` replaces the PowerShell helpers |

Behaviour changes: `integrate` needs `--with` and a shield folder in the target; approval payloads for `integration.apply` carry `groups`;
`ogx guardian evolve` recognises the new source layout. New: `ogx guard ...`, `ogx observe ...`, `init.py`.
Package name (`ogx`), CLI name (`ogx`), report formats and rule ids are unchanged.
