# Troubleshooting

## `ogx` is not recognized

Use the interpreter that owns the installation:

```bash
python -m ogx --version
```

On Windows the console script lives in `.venv/Scripts/ogx.exe`; on POSIX in
`.venv/bin/ogx`. Activate the environment or call the path directly. Do not
install a second copy into the system interpreter to "fix" this.

## Version mismatch between source and drop-in

```bash
python _dev/tools/sync_core_from_src.py --check
```

If it reports a mismatch, update `VERSION`, `src/ogx/__init__.py` and
`core/ogx/__init__.py` together. Never edit generated copies by hand; the tool
is read-only by design.

## `python -m build` fails

If the repository contains a `build/` directory, it shadows the `build` module:

```text
No module named build.__main__; 'build' is a package and cannot be directly executed
```

Build with pip instead:

```bash
python -m pip wheel . --no-deps --no-build-isolation -w dist
```

or remove the stray `build/` directory (it is a build artifact, not source).

## Tests skip on Windows

The suite reports skips such as:

```text
Directory symlinks are not available on this platform
Symlinks not supported on this platform
```

These are platform gates for symlink and POSIX file-mode checks, not failures.
They must remain skips; do not delete the tests or force them to pass. On Linux
and macOS the same tests are expected to run.## Provider says `requires_auth`

The adapter is implemented but the environment has no credential.
 Set the
provider-specific variable, for example:

```bash
export OGX_GITHUB_TOKEN=...        # GitHub / GitHub Enterprise
export OGX_GITLAB_TOKEN=...        # GitLab
```

OGX never reads credentials from the analyzed project and never prints them.
`ogx platform status` shows `Credentials present: N`.

## Provider says `descriptor_only` or `unsupported`

That is truthful. Twenty-one of twenty-eight providers expose identity only and
implement no domain operation. A descriptor cannot be made operational by
adding a token. See `docs/PROVIDER_ARCHITECTURE.md`.

## `ogx host list` prints “No hosts registered.”

Expected on a fresh state directory. Local filesystem and Git hosts are
**operational (read-only)**: they connect, read, report health, recover and run
through the CLI, but they do not support writes, execution, branch operations
or remote/network access. Registering a host does not make it verified; see
`docs/HOST_ARCHITECTURE.md`.

Registration requires `--identifier` for local hosts and rejects an empty root.
Sessions are process-local; after disconnect or a restart the session is gone,
and the coarse persisted status will show `disconnected` until the next
registration.

## `identity create` refuses to overwrite

An identity already exists at `.guardian-x/identity.json`. Use `--confirm` only
when you intend to replace it. To repair a damaged file, prefer
`ogx identity recover`; to keep the same project UUID, use
`--project-id <uuid>`.

## A write is refused without approval

Remote writes require a validated plan and `approved=True`; destructive
operations are not implemented at all. A denial is a result, not a bug. Do not
look for an environment variable that bypasses the gate.

## Sandbox command fails

`ogx sandbox run` requires Docker and a pre-pulled image digest. Without Docker
(on Windows without Docker Desktop, for example) OGX reports the capability as
unavailable and does not fall back to running the command on the host. That
failure mode is intentional.

## Where are reports and audit records?

Under the OGX state directory (`OGX_HOME`, or the default user data path printed
by `ogx init`), never inside the scanned repository. Provider audit events are
appended to `<state_dir>/platform/audit.jsonl`.

## Drop-in `init.py status` fails

Run it from the extracted `omega-shield` directory. If you extracted the zip
inside `GUARDIAN-X/` and rebuilt the zip afterwards, the archive may contain
your temporary files; rebuild from a clean tree. The archive must contain
`omega-shield/init.py` and `omega-shield/core/ogx/__init__.py`.

## Windows cannot see a `/tmp` path passed to Python

Git Bash maps `/tmp` differently from native Windows Python. Use paths inside
the project (for example `dist/`) or `cygpath -w`. A `FileNotFoundError` for
`/tmp/...` usually means the file was written by a native tool to a different
location.

## Ruby validator is unavailable

`_dev/tools/validate_config.rb` requires Ruby. If Ruby is not installed, the
check cannot run; report it as not run rather than passing it. It is not part of
the Python package or the runtime.

## Reporting a problem

Include the command, the exact error, the OGX version (`ogx --version`), the
Python version and the operating system. Do not paste tokens or `.env`
contents. A clean report of a failure is more useful than a fabricated success.
