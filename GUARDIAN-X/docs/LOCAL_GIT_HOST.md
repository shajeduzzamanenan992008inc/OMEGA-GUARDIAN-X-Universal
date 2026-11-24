# Local Git Host

Date: 2026-11-24
Implementation: `src/ogx/hosts/adapters/git_runtime.py`
Status: **OPERATIONAL (read-only, local-only)** for the tested scope.

## 1. Supported operations

- `connect` / `disconnect` against a configured repo directory.
- `validate()` via `git rev-parse --show-toplevel` (not the bare boolean
  `--is-inside-work-tree`).
- Read-only adaptors: `metadata()`, `exists(path)`, `file(path, max_bytes)`,
  `tree(path)`, `list_branches()`, `list_tags()`, `branch_info(branch)`,
  `file_history(path, max_entries)`, `list_commits(**kwargs)`.

No write, branch create/delete, merge, rebasing, push/pull/fetch and no
remote/network command exists. The adapter issues no Git CLI argument containing
form input.

## 2. Security model

1. **Safe invocation.** `run_git` builds one `subprocess.run` argument list
   where the directory is never interpolated into shell. All other arguments
   are literal constants the adapter controls; none of them are user-controlled
   strings.
2. **No remote/network.** No fetch/push/pull/clone/remote/submodule/add urls are
   ever constructed. The adapter is strictly offline for the connected worktree.
3. **Bounded execution.** Every call uses a bounded timeout (default 60 s).
4. **Output limits.** File reads cap at `max_bytes`; commit history caps at
   `max_entries`. Tree listing caps entries. Output is not sent to audit logs
   in full; read content is truncated by the call.
5. **Corruption handling.** A corrupted repo does not produce git output; the
   adapter raises `HostValidationError` / `HostIOError` on failure. It never
   prints raw git stderr into logs.
6. **No secrets in logs.** Identity lookups return the string from git itself;
   repository content reads go to the caller, not the audit sink.
7. **No sources outside `.git`.** Path containment follows the FS adapter model:
   a path outside the repo root is rejected. The adapter does not dereference
   the `.git` directory as a useful host for host file reads; repo reads are
   scoped to the worktree.

## 3. Edge-case handling

- **Non-git directory:** `setupadapters.` raises `HostValidationError` before
  any API use.
- **All `.git` removed:** after real `.git`, second `rev-parse --show-toplevel`
  discovers no toplevel; `validate()` raises `HostValidationError`.
- **Empty repo:** `HEAD` points at an unborn ref; branch listing returns a
  synthetic `(None, "Unborn")` ref. All other read operations raise
  `UnsupportedCapabilityError`.
- **Detached HEAD:** `branch_info`-returns named remote-tracking info plus a
  synthetic `(HEAD, detached)` marker. Use it read-only.
- **Missing file:** `exists/file` return false/None; `list_branches/tags/commits`
  return empty counts as needed.

## 4. Platform behaviour

- Git CLI `git` binary available on PATH; `rev-parse` and porcelain `--short`
  used.
- Windows `devkit/` base/.git path is used for integration tests; the same
  validation path is used for all local Git host tests.
- Empty/detached repos, missing-file and name+object pairs are exercised.

## 5. Test evidence

`tests/test_host_git_runtime.py`: 21 passed, ~39 s.
Covered: invalid directory, missing `.git`, detached HEAD, missing file, file
read/decode and binary, symlink containment by root, tree listing (with and
without disconnected session), commit history bounds, list_branches/tags and
branch_info, empty repo synthetic refs, health, validation round-trips, and
failed recovery.

## 6. Limitations

- Read-only; write/branch/remote operations are unsupported by design.
- `file_history` and `list_commits` return up to configured limits; they are
  not paginated cursors.
- Git identity/authorship reflects the local repo only; no authentication or
  remote sync is performed.
- Tree reads do not recurse; they surface the indicated directory.
- No performance/runtime indicators beyond read success; large repos are still
  local but reads are bounded.
