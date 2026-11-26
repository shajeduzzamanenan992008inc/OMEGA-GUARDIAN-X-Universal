# Identity Lifecycle: Explain, Recover, Fork

**Status:** implemented and covered by tests
(`tests/test_identity_lineage.py`, `tests/test_identity_lifecycle_cli.py`).
**Modules:** `src/ogx/pap/lineage.py`, `src/ogx/pap/identity.py`,
`src/ogx/registry/v2.py`.
**Schema:** identity schema `1.0` is unchanged.

## Evidence explanation

`ogx identity explain [path]` answers "why does GUARDIAN-X believe this is a
project?" without writing anything.

```bash
ogx identity explain /work/project --json
```

Evidence considered:

| Evidence | Source | Weight |
| --- | --- | --- |
| Identity file + hash | `.guardian-x/identity.json` | Strong |
| Registry project | project id / identity hash / workspace location | Supporting |
| Integration manifest | `OMEGA GUARDIAN X/integration-manifest.json` | Supporting |
| Lineage record | `.guardian-x/lineage.json` | Supporting |

Classifications:

| Classification | Meaning | Action |
| --- | --- | --- |
| `verified` | Valid identity file with matching hash | none |
| `provisional` | No identity file, but registry/manifest evidence exists | adopt explicitly with `identity create --project-id` |
| `conflict` | Evidence disagrees | do not merge; resolve or fork |
| `missing` | No usable evidence | `identity create` or `identity recover` |

Exit code is `1` for `conflict`, `0` otherwise, so a conflict can gate scripts.
Conflicts are reported and never auto-resolved.

## Evidence-based recovery

`ogx identity recover --confirm` now passes real evidence to the recovery
engine instead of always creating a fresh identity:

1. valid identity file → returned unchanged;
2. registry project → identity restored with the registry `project_id`;
3. integration manifest `project_id` → identity created with that id;
4. no evidence → a new identity, only with `--confirm`, and the output says
   `evidence: new identity (no strong evidence)` so the operator knows.

Only UUID **v4** ids can become identity files (schema rule). Deterministic
provisional ids from V1 migration are therefore refused with an explanatory
message: they must be adopted deliberately, not silently promoted.

## Explicit fork and lineage

`ogx identity fork` creates a **new** project identity from a copy of an
existing project.

```bash
ogx identity fork /work/parent --destination /work/child \
  --name "Child Project" --confirm --json
```

Guarantees:

- the source is never modified or deleted;
- the destination must not exist and must not be inside the source;
- the child gets a new UUID v4 `project_id` and its own `identity_hash`;
- `.guardian-x/lineage.json` records `relationship: fork`,
  `parent_project_id`, `parent_identity_hash`, `parent_project_name`,
  `forked_at`, `forked_by` and `child_project_id`;
- `--confirm` is mandatory, because an identity is being created.

Lineage is a separate file on purpose: adding fields to `identity.json` would
break schema `1.0` compatibility. A lineage record is evidence of descent, not
a shared identity, and `explain` flags a lineage file whose
`parent_project_id` equals the project's own id as a conflict.

## Deterministic V1 → V2 migration

`RegistryV2.migrate_from_v1` derives ids from the V1 registry location and each
entry's `(name, resolved path)`, using UUID v5:

- window id: SHA-256 of the V1 registry **content** (a touched but unchanged
  file keeps its window id);
- provisional project id when no identity file exists: deterministic, and
  reported in `provisional_projects`;
- workspace and repository ids: deterministic from the same evidence;
- `patch_kinds` reports which patches actually ran
  (`id-fallback`, `workspace`, `repository`).

Re-running the migration over the same V1 registry is idempotent: rows are
upserted by id, so no duplicate projects appear. `dry_run=True` writes no
database rows and no backup, and returns the same window id on repeated calls.

A provisional (UUID v5) project id cannot be written into `identity.json`
(schema requires v4). That is intentional: provisional identity stays
provisional until an operator adopts a real identity.
