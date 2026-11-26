# Relocation Engine

**Status:** implemented and covered by tests
(`tests/test_relocation_engine.py`, `tests/test_identity_lifecycle_cli.py`,
`tests/test_migration_determinism.py`).
**Module:** `src/ogx/relocation.py`
**Journal:** `<state root>/relocation/<plan_id>.json`

## Model

```text
plan  ->  apply (journaled)  ->  verify
              |                    |
              +--> recover (after interruption)
              +--> rollback (on ordinary failure)
```

A relocation moves or copies a project tree **without changing its identity**.
`project_id` and `identity_hash` must be identical before and after; the engine
refuses to run when the source has no valid identity.

| Operation | Copies tree | Retains source | May delete source |
| --- | --- | --- | --- |
| `move` | yes | yes, by default | only with `--delete-source` |
| `copy` | yes | always | never |
| `clone` | yes | always | never |
| fork (identity) | yes | always | never; creates a **new** project id |

`copy`/`clone` here are workspace-level duplicates that keep the same project
identity (a second working copy). A **fork** is the only operation that creates
a new identity, and it is explicit: `ogx identity fork` (see
`docs/IDENTITY_LIFECYCLE.md`).

## Safety checks (planning)

`plan_relocation()` refuses, before any write, when:

- the source is missing, a symlink, or not a directory;
- the destination already exists (no silent merge of two trees);
- source and destination resolve to the same path;
- the destination is inside the source, or the source inside the destination;
- the source has no valid `.guardian-x/identity.json`;
- the nearest existing ancestor of the destination is not writable;
- the tree exceeds the bounded file count (200,000).

Planning also reports warnings (never silent): skipped symlinks, unreadable
files, non-regular files, and a read-only source on a `move`.

Symbolic links are **never followed or copied**; the destination is checked for
symlinked parent directories before each write (`symlink_escape`).

## Journal and recovery

Every step is persisted *before* it runs and again when it completes:

```text
preflight -> copy -> verify -> rebind_workspace -> delete_source -> finalize
```

The journal is written atomically (temp file + replace). If the process is
interrupted, `ogx relocate recover <journal>` resumes from the first unfinished
step. Copying is idempotent and hash-verified, so a partially copied tree is
completed file by file instead of being discarded.

- An **ordinary failure** (for example a disk error) rolls the destination back,
  but only when this run created it and no source was deleted.
- A **simulated interruption** (`SimulatedInterruption`) never rolls back: it
  leaves exactly what a crashed process would leave, which is what the recovery
  tests exercise.
- Recovery never rolls back, so an operator keeps the partial evidence.

## Source deletion

Deleting the source requires **all** of:

1. `operation == "move"`,
2. `confirm_delete_source=True` (`--delete-source`),
3. the source path not being a drive/root,
4. the source identity still matching the plan.

Otherwise `delete_source` is journaled as `skipped` with an explicit reason
(`source_retained_for_copy_or_clone` or
`explicit_confirmation_required`). The default is always "retain the source".

## Registry rebinding

After a verified copy, the workspace row whose normalized location equals the
source path is rebound to the destination. When no registry is supplied, the
step is journaled as `skipped: no_registry`; nothing is invented.

## CLI

```bash
ogx relocate plan  /work/project /media/usb/project --operation copy --json
ogx relocate apply /work/project /media/usb/project --operation copy --no-registry
ogx relocate verify /work/project /media/usb/project --json
ogx relocate recover <state>/relocation/<plan_id>.json
```

`apply` prints `source_deleted` and `delete_reason` so the outcome is explicit.
Exit codes: `0` success, `1` relocation failure (journal left for recovery),
`2` refused by a safety check or invalid input.

## Verification

`verify_relocation(source, destination)` compares:

- `project_id` and `identity_hash` (`identity_preserved`),
- the SHA-256 of every file in both trees (`content_matches`),
- missing/extra/differing paths.

## Known limits

- Cross-drive and USB relocation were **not** executed on a second physical
  volume; the same code paths are exercised on temporary directories only.
- `os.access(W_OK)` read-only detection is advisory on Windows.
- The engine is synchronous; it does not attempt to keep a running process
  alive across the move.
