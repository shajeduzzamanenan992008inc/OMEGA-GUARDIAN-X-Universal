# Project Identity

## 1. Guarantee

A project keeps one identity when it moves between:

```text
C:\Project  →  D:\Project  →  E:\Projects\Project  →  USB:\Project
local clone →  GitHub/GitLab/Bitbucket repository  →  cloud workspace
```

`project_id` and `identity_hash` do not change. Workspace **locations** may
change, and repository **links** point at the same `project_id` regardless of
where the working copy lives.

## 2. Identity file

Location: `<project>/.guardian-x/identity.json`.

Recorded fields (from `ogx identity show`):

| Field | Meaning |
| --- | --- |
| `schema_version` | Identity document schema |
| `project_id` | Stable UUID for the project |
| `project_name` | Human-readable name |
| `description` | Optional description |
| `created_at` | Creation timestamp |
| `created_by` | Actor identity |
| `ogx_version` | Version that created the identity |
| `identity_hash` | Integrity hash over identity content |

`ogx identity show` prints `hash_valid: True|False`; `ogx identity validate`
reports errors and warnings. No absolute path or drive letter is stored, which
is why relocation preserves identity.

## 3. CLI

```bash
ogx identity create . --name "Project" [--description TEXT]
                     [--created-by ACTOR] [--project-id UUID] [--confirm]
ogx identity show .
ogx identity validate .
ogx identity recover .
ogx identity update .
```

- `create` refuses to overwrite an existing identity without `--confirm`.
- `--project-id` exists for recovery from a known UUID.
- `show`/`validate` support JSON output.
- `recover` repairs a missing/corrupted identity file.

## 4. Repository linking (URL is not identity)

`ogx.platform.identity`:

- `normalize_repository_url(url)` produces a credential-free, lowercase key:
  `host/path` with `.git` stripped. `git@host:path` and scheme URLs are
  accepted; URLs with embedded userinfo are rejected. A non-default explicit
  port is part of the key (`git.example.com:8443/acme/repo`), while an explicit
  default port (`:443` for HTTPS, `:80` for HTTP) normalizes away. IPv6
  endpoints keep brackets and port (`[2001:db8::1]:8443/acme/repo`).
- `deterministic_repository_id(provider_id, normalized)` uses a fixed namespace
  (`uuid5`) so the same repository yields the same row id in any process.
- `link_repository_to_project(...)`:
  - idempotent for the same project;
  - raises `DuplicateEntityError` if the repository is already linked to a
    **different** project;
  - never re-parents silently and never merges projects.
- `assert_relocation_safe(project_id)` reports that `project_id` and
  `identity_hash` are unchanged and lists linked repositories/workspaces.

Repository rows store the original URL plus the normalized URL; lookup and
duplicate detection use the normalized form.

## 5. Storage

Registry V2 (`OGX_HOME/registry/registry.db`, SQLite, schema version 2):

| Table | Identity-relevant columns |
| --- | --- |
| `projects` | `project_id` (PK), `canonical_name`, `identity_hash`, `lifecycle_state` |
| `repositories` | `repository_id` (PK), `project_id` (FK), `provider`, `repository_url`, `normalized_repository_url` |
| `workspaces` | workspace id, `project_id` (FK), mutable location/status |
| `hosts` | host records and capability metadata |

Foreign keys are enabled, the journal is WAL, and writes run inside
`transaction()`; failures roll back.

## 6. Relocation and conflict handling

- Moving/renaming the working copy changes only the workspace location; the
  project row is untouched.
- Cloning the repository elsewhere and linking it again returns the existing
  link if the project matches, and raises a conflict if it does not.
- Two different projects never share one normalized repository key. Resolution
  is explicit re-linking by an operator, not automatic.
- Registry integrity can be checked (`ogx registry integrity`) and restored
  from backup (`ogx registry backup` / `recover`).

## 7. Verified scenarios

| Scenario | Status | Evidence |
| --- | --- | --- |
| Directory rename / move within one filesystem | TESTED | `tests/test_intelligence_integration.py::test_identity_is_preserved_and_survives_relocation` |
| Workspace location update (relocation) | TESTED | `tests/test_registry.py` (workspace location update) |
| Identity create/show/validate/recover | TESTED | `tests/test_identity.py` |
| Duplicate repository link to another project | TESTED (raises conflict) | `tests/test_registry.py`, identity linking tests |
| C: → D: drive letter change | UNVERIFIED locally (no second drive used) |
| USB / removable storage relocation | UNVERIFIED |
| Local → Git host and Git host → local | UNVERIFIED end-to-end (linking logic tested only) |
| Local → remote workspace, and workspace → workspace | UNVERIFIED; workspace adapters are descriptor-only |

Unverified scenarios must not be reported as passing. They require an
environment with the corresponding storage/host and a dedicated relocation
test.

## 8. Limits

- `identity_hash` detects accidental/corrupt edits; it is not a signature and
  does not prove who wrote the file.
- A copied `.guardian-x/identity.json` deliberately carries the project UUID;
  treat a copied control folder as the same project. Delete it to create a new
  identity deliberately.
- Host migration depends on host support; see `docs/HOST_ARCHITECTURE.md`.
