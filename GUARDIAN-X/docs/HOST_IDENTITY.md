# Host Identity (HID)

**Status:** implemented and covered by tests (`tests/test_host_identity.py`,
`tests/test_identity_lifecycle_cli.py`).
**Module:** `src/ogx/pap/host_identity.py`
**Storage:** `<state root>/host/host.json`

## What it answers

| Question | Answer |
| --- | --- |
| Which machine/user install is this? | `host_id` (UUID v5, deterministic from the binding) |
| Is this state directory really mine? | `verify_host_identity()` decision |
| Which project is this? | Not here — see `docs/PROJECT_IDENTITY.md` |
| Which working copy is this? | Not here — workspace rows in Registry V2 |

A host identity belongs to one machine/user install. It never travels with a
project, is never copied into a repository, and is never used as a project
identity.

## Privacy model

The binding is derived from:

- `platform.node()` (host name),
- the current user name,
- `platform.system()` and `platform.machine()`.

Only a SHA-256 digest of that evidence is persisted. **No hardware serial
numbers, MAC addresses, disk ids or machine GUIDs are read or stored**, and the
raw host/user names are not written to `host.json`. `display_name` defaults to
the literal `local-host`, never to the host name.

## Decisions

| State | Meaning | Access |
| --- | --- | --- |
| `verified` | Stored binding matches this machine | `read_write` |
| `weak` | No hostname or no user was available when the identity was created | `read_only_for_ownership_operations` |
| `foreign_or_renamed` | Stored binding does not match (copied state directory, or hostname/user changed) | `read_only` |
| `missing` | No `host.json` yet | `read_only` |

A weak binding still allows normal project work, but ownership-sensitive
operations must be refused: the tool cannot prove it is the same machine.

A mismatch is reported, **never auto-repaired**. `adopt_host_identity(...,
confirm=True)` re-binds `host.json` to the current machine only when the caller
explicitly asks for it. Because the binding is not a hardware id, a legitimate
hostname/user rename looks exactly like a copied state directory; the report
says `foreign_or_renamed` rather than pretending to distinguish them.

## Host ids are deterministic on purpose

`host_id = uuid5(HOST_ID_NAMESPACE, "host:" + binding_hash)`.

- Re-installing OGX on the same machine/user reuses the same HID.
- A state directory copied to another machine resolves to a different expected
  HID, which is what raises `foreign_or_renamed`.
- Two projects on the same machine share one machine HID, but keep distinct
  host *records* for their adapters (root identifiers).

## Host record registration is idempotent

`ogx host register` looks up an existing host by `(host_type, identifier)` and
reuses its `host_id`. Re-registering the same root no longer creates a duplicate
row (P0 conflict `CX-03` / `RT-16`). The record's metadata also stores the
machine `host_id` so adapter records can be traced to the machine identity.

```bash
ogx host register --type local_filesystem --provider local \
  --name Project --identifier /work/project --json
# {"host_id": "...", "machine_host_id": "...", "registration": "new"|"updated"}

ogx host identity          # show host.json and its verification
ogx host identity --json
```

Exit codes: `0` when the machine binding is `verified` or `weak`, `1` when the
state directory is foreign (read-only), `2` on error.

## Known limits

- The binding is *evidence of continuity*, not proof of ownership. It is not a
  signature and it is not resistant to an attacker who can edit `host.json`.
- Real second-machine behaviour was not exercised on a second physical machine;
  the decision logic is covered by tests with synthetic bindings only.
