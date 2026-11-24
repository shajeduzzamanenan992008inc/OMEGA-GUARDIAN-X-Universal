# Dependency Boundaries

## 1. Runtime dependency policy

`pyproject.toml` declares `dependencies = []`. The shipped package and the
drop-in tree use the Python standard library only. Build tooling requires
`setuptools>=77`.

Consequences:

- No vendor SDK (boto3, Azure SDK, google-cloud-*, python-gitlab, PyGithub) may
  be imported by `src/ogx` or `core/ogx`.
- No third-party HTTP client (`requests`, `httpx`, `aiohttp`) may be imported.
  Network access uses `urllib` inside `ogx.platform.transport` and the OSV
  client.
- No third-party configuration parser. TOML writing is implemented in
  `ogx.discovery` with the standard library; YAML is not required at runtime.
- No `jsonschema` dependency. PAP schema validation is implemented with
  standard-library checks.

A test run in a clean environment fails if an undeclared import is added,
because nothing else is installed.

## 2. Allowed dependency direction

```text
ogx.cli
  └── engines / scanner / pipeline / intelligence / reports / guiardian flows
        └── models / rules / policy / findings / config
              └── (standard library)

ogx.platform.adapters.*
  └── ogx.platform.base, transport, security, models, errors
        └── ogx.registry (identity linking only)
              └── (standard library)

ogx.hosts (compatibility facade)
  └── ogx.platform.adapters.*

ogx.registry.v2
  └── ogx.registry.models, ogx.registry.exceptions, ogx.config
        └── (standard library)
```

Rules:

1. `ogx.platform` never imports the CLI, the analyzers, or the project it is
   analyzing.
2. `ogx.hosts` must not contain a second provider implementation; it re-exports
   the canonical adapters.
3. `ogx.registry` must not import `ogx.platform` at module import time
   (`ogx.platform.identity` imports the registry lazily inside functions).
4. Adapters may import platform contracts; platform contracts must not import a
   specific adapter at module level. Registry construction imports adapters.
5. CLI modules may import engines; engines must not import CLI modules.
6. No module may import from `tests/` or `_dev/`.
7. No runtime module may import a vendor SDK or a third-party HTTP/TOML/YAML
   library.

## 3. Drop-in parity boundary

`core/ogx` is a hand-maintained drop-in, not a build artifact. It must:

- use the standard library only;
- expose the same package version (`VERSION`, `src/ogx/__init__.py`,
  `core/ogx/__init__.py` all agree);
- contain no nested duplicate tree;
- exclude `tests/`, `_dev/`, `src/`, `.github/`, `release/`, `dist/`, local
  secrets, databases and compiled artifacts from its distribution zip.

Gate:

```bash
python _dev/tools/sync_core_from_src.py --check
```

Expected: `OK: source and drop-in structure, versions and packaging
declarations agree.`

## 4. Optional external tools (not package dependencies)

These are detected, never required, and never vendored:

| Tool | Use | Failure mode |
| --- | --- | --- |
| Docker | `ogx sandbox run` disposable container | Reports unavailability; does not fall back to host execution |
| OSV API | `ogx osv-audit` live advisory lookup | Optional; bounded HTTP; no fallback claim |
| Git | `ogx repo`, `ogx impact` | Reported as unavailable metadata |
| Ruby | `_dev/tools/validate_config.rb` | Not executed on hosts without Ruby |
| TShark/Binwalk/Unblob etc. | External analysis workflows | Not used by this package |

## 5. Network boundary

Only these modules may perform outbound requests:

- `ogx.platform.transport` (provider traffic);
- `ogx.osv` (explicit `osv-audit` command).

Both must be bounded (timeouts, size caps), must not follow redirects into
unexpected hosts, and must not load credentials from the analyzed project.
Scanning a local repository performs no network I/O.

## 6. Enforcement

`_dev/tests/test_architecture.py` checks:

- both trees exist with the expected exact package/module sets;
- no nested duplicate `core/ogx/core/` tree;
- stdlib-only imports;
- no import cycles;
- version agreement across the three version sources;
- packaging declarations (console script, package discovery, license files).

Because these checks are tests rather than conventions, an architecture
regression fails the suite instead of silently shipping.
