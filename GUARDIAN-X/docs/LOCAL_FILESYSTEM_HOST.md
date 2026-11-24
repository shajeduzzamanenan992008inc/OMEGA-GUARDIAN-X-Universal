# Local Filesystem Host

Date: 2026-11-24
Implementation: `src/ogx/hosts/adapters/filesystem_runtime.py`
Status: **OPERATIONAL (read-only)** for the tested scope.

## 1. Supported operations

- `connect` / `disconnect` against a configured root directory.
- Canonical root resolution and read/execute permission validation.
- `list_directory(path)` with bounded entries and per-entry metadata.
- `read_file(path, max_bytes)` with UTF-8 decoding, binary detection,
  truncation flag and SHA-256 of returned bytes.
- `metadata()` (root and read-only mode).
- `discover_projects(limit)` via the existing discovery engine.
- Adapter-specific `health`.

No write, delete, execute, move, copy, archive or network operation exists.

## 2. Security model

1. **Allowed roots.** The session root plus optional `allowed_roots`. All roots
   are resolved once.
2. **Traversal prevention.** Absolute paths, drive paths, UNC/`\\` paths, NUL
   bytes and any `..` segment are rejected before resolution.
3. **Containment.** The candidate is resolved (`strict=True`) and checked with
   `os.path.commonpath` against every allowed root using `os.path.normcase`.
   This handles Windows case folding and different-drive `ValueError`.
4. **Symlink escape.** Because containment is checked after `resolve()`, a
   symlink pointing outside the root is rejected for reads and marked
   `escapes_root` in listings. It is not followed.
5. **Limits.** `max_file_bytes` (default 256 KiB) truncates reads;
   `max_directory_entries` (default 500) bounds listings.
6. **Encoding.** Text is decoded UTF-8 with replacement; NUL-containing content
   is returned as `binary` with `text = None`.
7. **Permissions.** `PermissionError` becomes `HostPermissionError`; the session
   is not marked healthy after a failed root check.
8. **No secrets in logs.** The adapter logs no file content; audit events carry
   identifiers and state only.

## 3. Health and recovery

Healthy only when the resolved root exists, is a directory, is readable and
still resolves to the same canonical path. Otherwise `UNREACHABLE` with issues.
Recovery revalidates and reconnects; recreating the root makes recovery succeed.

## 4. Test evidence

`tests/test_host_filesystem_runtime.py`: 16 passed, 3 skipped (Windows
symlink/UNC platform gates). Covered: valid/missing/file root, traversal,
absolute and UNC rejection, missing paths, directory-as-file, truncation,
binary, symlink escape, in-root symlink, disconnected reads, read-only
capability truth, health degradation and recovery, failed recovery.

## 5. Limitations

- Windows symlink tests are skipped without symlink privilege; POSIX permission
  semantics are not exercised on this host.
- Very large directory trees are truncated, not paginated.
- `discover_projects` delegates to the discovery engine and inherits its
  heuristics and cost.
- No file watching, no write capability, no execution.
