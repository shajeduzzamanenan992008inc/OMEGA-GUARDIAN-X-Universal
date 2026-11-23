# Configuration and storage

OGX runtime state, registry, global configuration, and reports live outside target repositories. Normal scan/detect/start operations are read-only. An explicit, opt-in link (`ogx integrate`, groups chosen with `--with`) can create missing AI-agent instructions and Dependabot/Renovate configuration after the user reviews a plan and supplies `--confirm`; the project policy is the shield folder's `shield.toml`.

## External state directory

- Windows: `%LOCALAPPDATA%\\OMEGA-GUARDIAN-X`
- Linux/macOS: `$XDG_STATE_HOME/omega-guardian-x`, falling back to `~/.local/state/omega-guardian-x`
- Override: `OGX_HOME=/absolute/path/to/ogx-state`

Contents:

```text
<OGX state>/
├── config.toml
├── projects.json
└── projects/
    └── <safe-project-name>-<canonical-path-hash>/
        └── reports/
            ├── scan-<timestamp>-<id>.json
            └── scan-<timestamp>-<id>.md
```

`config.toml` contains global scan limits and exclusions:

```toml
[scan]
exclude = [".git", "node_modules", ".venv", "venv", "dist", "build", "vendor"]
max_files = 12000
max_file_bytes = 2000000
```

OGX rejects symlinked global config/registry/report paths. Project registration only stores a canonical path in the external registry. It does not create or change anything in that project. Only the separate, explicitly approved integration writes project-root governance files.

## Commands

- `ogx init`: initialize global OGX config only.
- `ogx projects add PATH --name NAME`: register a repository externally.
- `ogx projects list`: list registered paths.
- `ogx detect PATH`: identify project indicators.
- `ogx scan PATH`: run a read-only scan and save reports externally.
- `ogx scan --project NAME`: scan a registered project.
- `ogx start PATH`: safe scan alias; no project initialization.
- `ogx integrate plan PATH`: preview project-root governance files without writing.
- `ogx integrate apply PATH --confirm`: create only missing, allowlisted project-control files.
- `ogx integrate status PATH`: verify OGX-managed files using SHA-256.
- `ogx integrate remove PATH --confirm`: remove only unchanged OGX-managed files; user-modified files are preserved.

Scan limits are global in the current alpha. The project-local `omega-shield/shield.toml` is restrict-only policy plus governance guidance for supported agents; the scanner deliberately does not load untrusted project-local policy automatically. Enforced per-project scanner profiles are planned for a later release.

## Private `.env` configuration

OGX can load its private runtime settings from one dotenv file. By default the file is stored in the OGX external state directory, not in the repository being scanned:

- Windows: `%LOCALAPPDATA%\OMEGA-GUARDIAN-X\.env`
- Linux/macOS: `$XDG_STATE_HOME/omega-guardian-x/.env`, falling back to `~/.local/state/omega-guardian-x/.env`
- Override: set `OGX_ENV_FILE` in the operating-system environment to an explicit file path. `OGX_HOME` and `OGX_ENV_FILE` are bootstrap settings and must be set outside the `.env` file.

Create and validate the template:

```bash
ogx env init
ogx env path
ogx env check
```

`ogx env init` never overwrites an existing file and creates a private file (POSIX mode `0600`). On POSIX systems, `ogx env check` returns a warning/error status if group/other permissions are enabled; use `chmod 600 <path>` to restrict access. `ogx env check` validates mail sender/approver identities, mail provider credentials, approval expiry and the write-approval bypass flag without printing secret values. The operating-system environment takes precedence over values in `.env`.

OGX deliberately does **not** search the current directory or a target project's `.env` automatically. This prevents an untrusted repository from silently changing OGX credentials or safety settings. The supported names are documented in `../.env.example`. `.env` files are ignored by Git; commit only `.env.example` with empty secret placeholders. Never add real API tokens, SMTP passwords or private credentials to the example or a public repository.
