# Architecture

## Current MVP

```text
CLI (ogx)
  ├── Project detector
  │     └── language / ecosystem / framework indicators
  ├── Safe file walker
  │     └── exclusions, size cap, file-count cap, no symlink traversal
  ├── Static analyzers
  │     ├── requirements.txt heuristics
  │     ├── package.json heuristics
  │     └── secret and secure-coding pattern checks
  └── Report writers
        ├── JSON
        └── Markdown
```

## Planned enterprise architecture

- **Core:** orchestration, policy evaluation, finding schema, provenance, plugin registry.
- **Adapters:** package-manager parsers, language AST analyzers, framework-specific rules.
- **Intelligence providers:** vulnerability feeds, license catalogs, SBOM formats, optional AI explanation providers.
- **Execution broker:** disposable sandbox profiles with OS/container enforcement, resource limits, network policy, and explicit approval.
- **Integrations:** CLI, IDE extensions, GitHub/GitLab CI, REST API, dashboard, enterprise identity and audit export.

Adapters must be passive by default. Any capability that executes code or accesses network must declare it, request a scoped capability, and run through a separately reviewed execution broker. A plugin must not receive implicit filesystem/network/secret access.

## Universal provider engine

```text
CLI (ogx providers / platform / host / remote)
  +-- ogx.platform.registry        capability matrix, descriptors, negotiation
        +-- adapters/              github, gitlab, bitbucket, gitea(+forgejo),
        |                          azure_devops, aws_codecommit, sourcehut*
        +-- transport.py           bounded HTTP, retries, host pinning
        +-- security.py            SSRF/endpoint validation, credential policy, redaction
        +-- models.py              provider-neutral values
        +-- identity.py            repository <-> project linking
        +-- operations.py          write plans, approval gate, audit trail
```

*`sourcehut` is descriptor-only. Family providers (workspaces, CI/CD, project
management, package registries, cloud) are descriptor-only too: they expose
identity but no domain operation.

`ogx.hosts` is a compatibility facade over these adapters; the earlier prototype
implementation is no longer authoritative.
