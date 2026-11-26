# Universal Relocation and Identity Architecture

**Milestone I6 — Design and Gap Analysis**

| Field | Value |
| --- | --- |
| Status | Design only. No production code, tests, configuration, packaging, or license metadata were changed. |
| Repository | `D:\FTP\OMEGA-GUARDIAN-X-Universal\GUARDIAN-X` |
| Baseline HEAD | `bfa5db6f52891ded0d7602a87efede286aad7927` |
| Baseline branch | `main` |
| Baseline working tree | Clean before this document was written |
| Date | 2026-11-26 |
| Scope | Unified, portable identity for local, cross-drive, USB, GitHub, cloud, and multi-host use; safe state continuity; canonical/drop-in parity |
| Out of scope | Implementation, migrations of live user data, automatic network cloning, signing infrastructure, provider API changes |

This document uses four evidence labels:

- **VERIFIED** — read directly from the source at the stated HEAD.
- **TESTED** — covered by an automated test that passed at the stated HEAD.
- **UNVERIFIED** — not exercised on real hardware or a real remote; must not be claimed as working.
- **PROPOSED** — a design decision to be implemented later; it does not exist in the product today.

`DECISION REQUIRED` marks questions that need owner approval before implementation.

---

## 1. Scope, constraints, and non-goals

### 1.1 Goal

Define one identity architecture that answers five separate questions without conflating them:

1. **Which project is this?** — immutable project identity.
2. **Which machine/install is this?** — host identity.
3. **Which working copy is this?** — workspace identity.
4. **Where is the working copy now?** — mutable workspace location.
5. **Where does OGX keep its own data?** — state location.

The architecture must preserve project identity across:

```text
C:\Project  ->  D:\Project  ->  E:\Projects\Project  ->  USB:\Project
local clone ->  GitHub/GitLab/Bitbucket  ->  cloud workspace
machine A   ->  machine B  ->  a new OS installation
```

while never claiming that a Git remote URL alone proves two workspaces are the same project, and never silently merging the state of two distinct project copies.

### 1.2 Hard constraints

- Read-only source investigation for this milestone. The only file written is this document.
- No source, test, configuration, packaging, or license changes.
- No stage, commit, push, merge, branch creation, or destructive filesystem operation.
- Existing uncommitted user work must be preserved.
- Cross-drive, USB, GitHub, cloud, and multi-host behavior may be described as **PROPOSED** but not as verified until a real test is executed and recorded.
- Canonical (`src/ogx`) and drop-in (`core/ogx` + `init.py`) must converge on one behavioral contract without pretending they share an implementation.

### 1.3 Non-goals

- This document does not implement the model.
- It does not migrate any existing registry, state directory, identity file, or manifest.
- It does not add a network protocol, a signing service, or a provider integration.
- It does not redefine the existing immutable identity schema `1.0` in place.
- It does not promise automatic recovery when evidence is ambiguous.

### 1.4 What this document does not claim

- It does **not** claim that cross-drive relocation works today.
- It does **not** claim that USB relocation works today.
- It does **not** claim that a project cloned from GitHub is automatically recognized as the same project.
- It does **not** claim that `identity_hash` proves authorship. It is an unkeyed integrity checksum, not a signature.
- It does **not** claim that drop-in mode has project identity today; it does not.

---

## 2. Executive summary

The current product has a strong *project identity file* but no complete *identity system*. The identity file is path-independent and its hash is verified on read. Around it, however, location is still used as the join key in several places:

- the v1 `projects.json` registry stores absolute paths;
- report directories are derived from a path hash on the canonical side;
- drop-in state is derived from `sha256(casefold(HOST_ROOT))`, so moving the project changes the state directory;
- link manifests store absolute `project_root` values and refuse to operate after a move;
- workspace records store locations but are not automatically rebound after relocation;
- host records use a fresh random UUID on each `host register` call;
- v1-to-v2 registry migration generates a random project UUID when no identity file exists.

The proposed architecture separates five identifiers and five lifecycles, adds a host-scoped workspace binding, introduces a crash-safe relocation protocol, makes state keyed by project identity rather than path, and defines a parity contract so the drop-in distribution can implement the same behavior with standard-library-only code.

The most important design rule is:

> **Location is evidence, never identity. A remote URL is repository evidence, never project identity. Identity conflicts are surfaced, never auto-merged.**

---

## 3. Gap analysis

| ID | Gap (verified at HEAD unless marked) | Evidence | Consequence | Proposed fix | Phase |
| --- | --- | --- | --- | --- | --- |
| G1 | No stable machine identity. `host register` uses `uuid4()` per invocation. | `src/ogx/cli.py` `cmd_host_register`; `src/ogx/hosts/registry.py` | Re-registering the same machine creates a new host; state cannot be reliably tied to a machine. | Host identity record (`host.json`) with UUID + machine binding; registration becomes idempotent. | 1 |
| G2 | Workspace identity is not bound to project identity. Workspace rows are created around a location; location is the lookup key. | `registry/v2.py`; `intelligence/integration.py` | Moving a project makes registry resolution by location fail even though the identity file is intact. | Workspace binding keyed by `workspace_id`; location is mutable metadata; relocation updates the binding. | 1–3 |
| G3 | Canonical state and reports are path-keyed. | `core/ogx/cli/common.py` report hash; v1 `projects.json` | Reports do not follow the project when it moves; two paths can create two report histories. | Project-ID-keyed report hierarchy with workspace dimension; legacy layout read-only. | 5 |
| G4 | Drop-in has no identity layer at all. State home is derived from the absolute host root. | `init.py` `state_home`; `core/ogx` tree | Moving a drop-in project changes its state directory; no project ID survives. | Stdlib-only identity + host + workspace modules in `core/ogx/kernel`; state keyed by project ID. | 4–5 |
| G5 | Link manifests store absolute `project_root` and refuse after a move. | `src/ogx/project_integration.py`; `core/ogx/ecosystem/integration.py` | A legitimate relocation looks like a foreign project; integration status/removal fails. | Manifest v2 with `project_id`, `identity_hash`, `workspace_id`, `root_hash`; v1 reader classifies `moved` instead of hard failure. | 3 |
| G6 | `identity recover` CLI ignores registry/manifest evidence even though the API accepts it. | `src/ogx/cli.py` `cmd_identity`; `src/ogx/pap/identity.py` `recover_identity` | Recovery with strong evidence still creates a new identity. | `identity recover --strategy auto` passes registry and manifest evidence; no silent new ID when evidence exists. | 1–2 |
| G7 | v1→v2 migration uses `uuid4()` when identity is missing, despite a comment saying “deterministic UUID from path”. | `registry/v2.py` `migrate_from_v1` | Migrated projects get unstable IDs; re-running migration can duplicate projects. | Deterministic provisional ID plus explicit `provisional` marker; require adoption to a real identity. | 2 |
| G8 | `assert_relocation_safe` only reports project invariants; it does not validate workspace paths. | `src/ogx/platform/identity.py` | A green report does not prove the workspace still resolves. | Relocation verifier checks identity, workspace location, registry links, and state pointers. | 3 |
| G9 | No relocation journal or transaction. | No relocation module | A crash mid-move can leave registry and filesystem inconsistent. | Plan/journal/apply/verify state machine with idempotent steps. | 3 |
| G10 | No copy vs move vs clone vs fork semantics. | Identity docs describe moves only | Duplicate copies can silently share identity and state. | Explicit operation model and decision matrix; fork creates a new project ID. | 1–3 |
| G11 | No cross-host state export/import. | No export command | Moving to another machine loses history or forces manual copying. | Explicit, versioned export bundle; never automatic. | 5 |
| G12 | State keys truncate SHA-256 to 48 bits; casefold-only derivation can collide. | `init.py` `[:12]`; report hash `[:16]` | Birthday-collision risk grows with many projects; two roots can share state. | Full 128-bit key for new layouts; collision marker and exact-root check; legacy keys retained. | 5 |
| G13 | Repository URL is normalized but is not sufficient proof of project identity. | `platform/identity.py`; docs already warn | URL spoofing or a recycled remote can merge unrelated state. | Keep URL as repository evidence only; lineage evidence and explicit adoption required for merges. | 1–3 |
| G14 | No canonical/drop-in identity parity tests. | `_dev` tests cover architecture, not identity behavior | The two distributions can drift silently. | Shared golden vectors and a parity test suite. | 0, 4 |

---

## 4. Current architecture (verified)

### 4.1 Canonical distribution

```text
                       Project root (location is mutable)
        ┌────────────────────────────────────────────────────────┐
        │  .guardian-x/identity.json                             │
        │    project_id (UUIDv4) + identity_hash (SHA-256)       │
        │    NO absolute path stored                             │
        │                                                        │
        │  OMEGA GUARDIAN X/integration-manifest.json            │
        │    project_root = absolute path  <-- breaks on move    │
        │    managed_files = relative path -> SHA-256            │
        └───────────────────────────┬────────────────────────────┘
                                    │ read by
                                    ▼
        ┌────────────────────────────────────────────────────────┐
        │  OGX_HOME  (per user, fixed; outside every project)    │
        │                                                        │
        │  projects.json                name -> absolute path    │
        │  registry/registry.db         SQLite schema v2:        │
        │    projects, repositories, workspaces, hosts,          │
        │    project_hosts                                       │
        │  projects/<safe-name>-<sha256(path)[:16]>/reports/     │
        └────────────────────────────────────────────────────────┘
```

Key code paths:

| Concern | Module | Behavior |
| --- | --- | --- |
| Identity create/read/hash | `src/ogx/pap/identity.py` | UUIDv4, canonical JSON, `sha256:` hash, size/secret/schema validation, immutable fields |
| Identity schema | `src/ogx/pap/identity_schema.py` | Schema `1.0`; strict allowed fields; 64 KiB limit; UUIDv4 and timestamp rules |
| Registry | `src/ogx/registry/v2.py` | SQLite schema version `2`; projects/repositories/workspaces/hosts |
| Identity resolution | `src/ogx/intelligence/integration.py` | identity > registry project > explicit ID > path-dependent fingerprint |
| Fallback fingerprint | `src/ogx/workspace.py` | `ogx-` + SHA-256 over `normcase(root)` + tree digest + sampled manifests |
| Relocation report | `src/ogx/platform/identity.py` | `assert_relocation_safe` returns counts and `relocation_independent: True` |
| Link manifest | `src/ogx/project_integration.py` | Absolute `project_root`; refuses a different root |
| Host registry | `src/ogx/hosts/registry.py` | Host rows, capabilities, project-host and repository-host links |
| Host CLI | `src/ogx/cli.py` | `host list/show/status/capabilities/register/inspect/connect/disconnect/health/recover/sessions/discover` |

### 4.2 Drop-in distribution

```text
                       Host project root (location is mutable)
        ┌────────────────────────────────────────────────────────┐
        │  omega-shield/  (or renamed shield folder)             │
        │    init.py                                             │
        │    core/ogx/          standard-library runtime         │
        │    link manifest      absolute project_root            │
        └───────────────────────────┬────────────────────────────┘
                                    │ init.py computes
                                    ▼
        ┌────────────────────────────────────────────────────────┐
        │  <LOCALAPPDATA>/omega-shield/                          │
        │    <slug>-sha256(casefold(HOST_ROOT))[:12]/            │
        │      projects.json         name -> absolute path       │
        │      projects/<name>-<pathhash>/reports/               │
        │                                                        │
        │  NO identity.json, NO project_id, NO registry DB       │
        └────────────────────────────────────────────────────────┘
```

Derivation details (verified):

- `HOST_ROOT = Path(os.environ.get("OMEGA_HOST_ROOT") or SHIELD_DIR.parent).resolve()`.
- `state_home()` returns `OGX_HOME` when set; otherwise Windows `%LOCALAPPDATA%\omega-shield\<slug>-<digest>` and POSIX `$XDG_STATE_HOME/omega-shield/<slug>-<digest>`.
- `digest = sha256(str(HOST_ROOT).casefold())[:12]` — 48 bits.
- `_prepare_env()` refuses a state root inside `HOST_ROOT` and creates the state directory.
- `init.py status` calls `_prepare_env(force=True)`, so even a read-only status call can create the external state directory. It does not write inside the host project.

### 4.3 Identity resolution today

```text
read .guardian-x/identity.json
        │
        ├── valid ──────────────► source = "identity"
        │                          project_id = identity.project_id
        │
        ├── missing/invalid ────► registry lookup
        │                          ├── by identity_hash (if identity exists)
        │                          ├── by explicit project_id
        │                          └── by workspace normalized location
        │
        ├── registry hit ───────► source = "registry"
        │
        ├── explicit ID ────────► source = "explicit"
        │
        └── nothing ────────────► source = "fingerprint"
                                   project_id = "ogx-" + sha256(...)[:24]
```

The fallback fingerprint includes `os.path.normcase(str(self.root))`. It therefore changes on relocation even when the file tree is identical. It is a workspace fingerprint, not a project identity.

### 4.4 Verified behaviors relevant to relocation

| Behavior | Status | Evidence |
| --- | --- | --- |
| Identity file stores no absolute path | VERIFIED | `ProjectIdentity.to_dict()` fields |
| Identity hash covers canonical JSON excluding `identity_hash` | VERIFIED | `to_canonical_json`, `compute_hash` |
| `project_id` is immutable UUIDv4 | VERIFIED / TESTED | `identity_schema.py`; `tests/test_identity.py` |
| `read_identity` rejects a hash mismatch | VERIFIED / TESTED | `tests/test_identity.py` |
| `identity update` allows only `project_name` and `description` | VERIFIED | `update_identity_field` |
| Registry workspace update exists but is manual | VERIFIED / TESTED | `update_workspace_location`; `tests/test_registry.py` |
| Canonical directory rename preserves identity | TESTED | `test_identity_is_preserved_and_survives_relocation` |
| Registry workspace location update | TESTED | `test_update_workspace_location` |
| Drop-in relocation preserves state | **UNVERIFIED / not implemented** | No identity layer in `core/ogx` |
| Cross-drive relocation | **UNVERIFIED** | No test on a second volume |
| USB relocation | **UNVERIFIED** | No removable-media test |
| GitHub/cloud round-trip | **UNVERIFIED** | No remote test |
| Host-to-host migration | **UNVERIFIED** | No second host test |
| v1→v2 migration stable project IDs | **FAILS for projects without identity** | `uuid4()` in `migrate_from_v1` |
| `identity recover` uses registry/manifest evidence via CLI | **NO** | `cmd_identity` passes only `confirm=True` |

### 4.5 What is already sound

The existing identity file is a good foundation:

1. It is path-independent.
2. It is content-addressed and validated on read.
3. It uses a random UUIDv4 rather than a name or path hash.
4. It refuses to overwrite without explicit confirmation.
5. It contains a schema and size limit, and rejects credential-like content.
6. The registry has the right tables and the right high-level concepts.

The defect is not the identity record. The defect is that the surrounding system still uses location as a join key and has no explicit workspace/host lifecycle.

---

## 5. Proposed architecture

### 5.1 Design principles

1. **Identity is portable; location is not.**
2. **A project has exactly one project identity for its lifetime.**
3. **A host identity belongs to the machine/user install and never travels with the project.**
4. **A workspace identity belongs to one working copy on one host.**
5. **State follows the project identity on a host; it does not follow a path and does not cross hosts automatically.**
6. **A repository URL is lineage evidence, never the primary key of a project.**
7. **Conflicts are quarantined and require an explicit decision.**
8. **Every state-changing relocation is planned, journaled, verified, and reversible.**
9. **Canonical and drop-in share a behavioral contract, not necessarily storage technology.**
10. **No silent merge, no silent fork, no silent adoption, no silent deletion.**

### 5.2 The five separations

| Concept | Symbol | Owner | Lifetime | Stored where | May change on relocation? |
| --- | --- | --- | --- | --- | --- |
| Project identity | PID | Project | Forever (unless explicit fork) | `.guardian-x/identity.json` | No |
| Project identity hash | PIH | Project | Recomputed only when mutable metadata changes | Same file | No, unless `project_name`/`description` changes |
| Host identity | HID | Machine/user install | Install lifetime | `<state>/host.json` | No |
| Workspace identity | WID | Host registry/state | Working-copy binding lifetime | Registry / state index | Retained on move; new on copy/clone/restore |
| Workspace location | LOC | Host registry/state | Mutable | Registry / state index | Yes |
| State location | STATE | Host/user install | Install lifetime | OS default or `OGX_HOME` | Independently movable; not project identity |
| Repository identity | RID | Registry | While the link exists | Registry | URL normalization may change; RID derived from it |

### 5.3 Proposed relationships

```text
                         ┌─────────────────────────────────────┐
                         │ PROJECT (portable, one per project) │
                         │ PID  = UUIDv4                       │
                         │ PIH  = sha256:... over identity     │
                         │ .guardian-x/identity.json           │
                         └───────────────┬─────────────────────┘
                                         │ 1
                                         │
             ┌───────────────────────────┼───────────────────────────┐
             │ N                         │ N                         │ N
             ▼                           ▼                           ▼
┌─────────────────────────┐  ┌─────────────────────────┐  ┌─────────────────────────┐
│ HOST (one machine/user) │  │ WORKSPACE (one copy)    │  │ REPOSITORY (lineage)    │
│ HID = UUIDv4            │◄─┤ WID = UUIDv4            │  │ RID = UUIDv5            │
│ host.json + machine     │ N│ LOC = mutable location  │N │ provider + normalized   │
│ binding                 │  │ status: active/missing  │  │ URL + optional lineage  │
└───────────┬─────────────┘  └───────────┬─────────────┘  └─────────────────────────┘
            │                            │
            │ owns                       │ owns
            ▼                            ▼
┌─────────────────────────┐  ┌─────────────────────────┐
│ STATE LOCATION          │  │ REPORTS / AUDIT / INDEX │
│ <state root>            │  │ keyed by PID, then WID  │
│ registry, host, exports │  │ never keyed by path     │
└─────────────────────────┘  └─────────────────────────┘

Cardinality:
  PROJECT 1 ── N WORKSPACE   (multiple copies are legitimate)
  HOST    1 ── N WORKSPACE
  PROJECT 1 ── N REPOSITORY  (remotes/mirrors)
  WORKSPACE N ── 0..1 REPOSITORY (the remote it was cloned from)
```

### 5.4 Identity levels

The level describes how much continuity the tool can actually prove. It prevents overclaiming.

| Level | Name | Required evidence | What survives | What does not |
| --- | --- | --- | --- | --- |
| L0 | Unmanaged | No identity file | Nothing project-specific | Everything |
| L1 | Local identity | Valid `identity.json` | PID/PIH across moves and copies | Host/workspace history |
| L2 | Host-bound | L1 + host record + workspace binding | PID/PIH + local report history + relocation audit | Other hosts |
| L3 | Cross-host verified | L2 + explicit export/import or signed attestation | PID/PIH + selected history on another host | Automatic merge; authenticity without a key |

Default behavior after implementation: create L2 on first bind; never claim L3 unless an explicit export/import or attestation occurred.

### 5.5 Evidence ladder

Resolution must prefer evidence in this order and explain the choice:

1. **Valid identity file** whose hash matches — authoritative for PID.
2. **Registry project with the same PID** — corroborating metadata, workspace links, repository links.
3. **Registry project with the same PIH** — strong evidence when the identity file changed only mutable metadata.
4. **Explicit user-provided PID** — accepted only with a warning if it conflicts with a valid identity file.
5. **Manifest `project_id`** — supporting evidence only; never sufficient alone.
6. **Workspace location match** — hint; valid only when the binding matches HID and the location is still live.
7. **Path/tree fingerprint** — provisional workspace label only; never a project identity.
8. **Repository URL** — lineage evidence only; never sufficient to establish or merge a PID.

Every resolution result must expose `source`, `confidence`, `warnings`, and `requires_confirmation`.

### 5.6 State topology

Proposed default state root (compatibility-preserving):

```text
<state root>/
├─ host.json                    HID + machine binding + binding strength
├─ shared.json                  present only when this root is explicitly shared
├─ config.toml
├─ registry/
│  └─ registry.db               schema v3 after migration
├─ projects/
│  └─ <project_id>/
│     ├─ project.json           cached identity summary + last verified PIH
│     ├─ index.json             workspace list + report index
│     └─ workspaces/
│        └─ <workspace_id>/
│           ├─ workspace.json   binding snapshot
│           ├─ reports/
│           └─ audit.jsonl
├─ relocations/
│  └─ <plan_id>.json            journal, state machine
├─ exports/                     explicit export bundles only
└─ legacy/                      read-only pointers for v1 state, never rewritten
```

Rules:

- The **state root is per host and per user** by default.
- If `shared.json` exists, all new writes go under `<state root>/hosts/<host_id>/...`; a state root without that marker is single-host.
- A state root whose `host.json` machine binding does not match the current machine is opened **read-only** and marked `foreign`; it is never silently reused.
- Reports for a project on the same host follow PID, not the path. Moving a project therefore keeps its report history.
- Reports from another host are not merged automatically. They arrive only through an explicit export/import with a manifest and hashes.

---

## 6. Identity data model

### 6.1 Project identity (unchanged, schema 1.0)

The existing file remains the single authority for PID. It is deliberately **not extended** in phase 1, because the v1.0 validator rejects unknown fields (`additionalProperties: false` and an exact allowed-field set). Adding fields in place would break every older reader.

`<project>/.guardian-x/identity.json`:

```json
{
  "schema_version": "1.0",
  "project_id": "5f0c2f7a-6d1e-4a3b-9c21-8f4d2e1b7a90",
  "project_name": "demo",
  "description": "",
  "created_at": "2026-11-26T12:00:00+00:00",
  "created_by": "user@example.com",
  "ogx_version": "0.8.0a1",
  "identity_hash": "sha256:..."
}
```

Properties:

- `project_id`: UUIDv4, immutable. Validation requires version 4 and RFC 4122 variant.
- `identity_hash`: `sha256:` plus 64 lowercase hex characters over canonical JSON of all fields except `identity_hash`.
- Canonicalization: UTF-8, sorted keys, no whitespace (`separators=(",", ":")`), `ensure_ascii=False`.
- Mutable: `project_name`, `description` only. Changing either recomputes the hash; PID does not change.
- Immutable: `schema_version`, `project_id`, `created_at`, `created_by`, `ogx_version`, `identity_hash`.
- Size limit: 64 KiB. UTF-8 only. Credential-like patterns are rejected.
- No path, drive letter, hostname, username directory, or remote URL is stored.

**Authority rule:** a valid identity file wins over registry metadata. A conflict is reported; it is not resolved by overwriting the file.

**Authenticity rule:** the hash detects accidental corruption and naive edits only. Anyone who can rewrite the file can recompute the hash. Authenticity requires an external signature or keyed attestation, which is a separate decision (D2).

### 6.2 Host identity (new)

`<state root>/host.json`:

```json
{
  "schema_version": "gx-host/1.0",
  "host_id": "8a1d4f62-2c3e-4b77-9f10-5a6d8e2c4b31",
  "label": "workstation-1",
  "created_at": "2026-11-26T12:00:00+00:00",
  "os_family": "windows",
  "machine_binding": "sha256:...",
  "binding_strength": "strong",
  "state_root_id": "3c9e...",
  "installation_scope": "user"
}
```

Rules:

- `host_id` is a random UUIDv4 created once per state root, not derived from hostname, username, or path.
- `machine_binding` is a best-effort hash of an OS machine identifier:
  - Windows: `HKLM\SOFTWARE\Microsoft\Cryptography\MachineGuid` when readable.
  - Linux: `/etc/machine-id` or `/var/lib/dbus/machine-id`.
  - macOS: `IOPlatformUUID` if obtainable without privileges.
  - If unavailable, `machine_binding` is empty and `binding_strength` is `weak` or `none`.
- `binding_strength` values: `strong` (OS machine ID + state root ID), `weak` (one of them), `none` (random only).
- Hostname is a label, never an identity. Renaming a machine does not change HID.
- Reinstalling the OS may change the machine binding. The design then requires either a new HID or an explicit `host adopt` with confirmation; it never silently reuses a HID on a mismatched machine.
- CI/container hosts set `ephemeral: true` and do not create persistent workspace history by default.

### 6.3 Workspace binding (new)

A workspace is one working copy of one project on one host. It is the join between PID, HID, and location.

Registry row (canonical) / JSON record (drop-in):

```json
{
  "schema_version": "gx-workspace/1.0",
  "workspace_id": "d2f7...",
  "project_id": "5f0c...",
  "host_id": "8a1d...",
  "identity_hash_at_bind": "sha256:...",
  "location": {
    "kind": "local_path",
    "value": "D:\\Work\\demo",
    "normalized": "d:/work/demo",
    "volume_id": "vol:...",
    "normalization_version": 2
  },
  "status": "active",
  "binding_generation": 3,
  "provenance": "moved",
  "created_at": "2026-11-20T09:00:00+00:00",
  "last_verified_at": "2026-11-26T12:00:00+00:00",
  "verification": {
    "method": "identity_file",
    "result": "match"
  }
}
```

Rules:

- `workspace_id` is a random UUIDv4. It is not derived from a path or volume, because those values are unstable and can be reused.
- `workspace_id` is **retained** for a move of the same directory. It is **new** for a copy, clone, restore to a new location, or first open on another host.
- `binding_generation` increments on every verified relocation.
- `identity_hash_at_bind` records the PIH observed at bind time, so a later identity edit can be distinguished from a forged file.
- `provenance` is one of `created`, `moved`, `copied`, `cloned`, `restored`, `imported`, `adopted`.
- A workspace whose location is missing is `missing`, never deleted. A workspace can be archived explicitly.
- At most one active binding per `(host_id, normalized location)` is allowed.
- At most one active binding per `(project_id, host_id, workspace_id)` is allowed.

### 6.4 Repository and lineage identity

Repository identity remains a deterministic function of provider plus normalized URL:

```text
RID = UUIDv5(namespace, provider_id + ":" + normalized_repository_url)
```

Normalization already:

- removes the scheme;
- removes `.git` and trailing slash;
- lowercases host and path;
- retains a non-default port;
- brackets IPv6 literals;
- rejects embedded credentials;
- treats `:443` HTTPS and `:80` HTTP as default and removes them.

Proposed additional lineage evidence, stored in the registry and never in `identity.json`:

```json
{
  "project_id": "5f0c...",
  "repository_id": "...",
  "provider_id": "github",
  "normalized_repository_url": "github.com/acme/demo",
  "lineage": {
    "root_commits": ["..."],
    "remote_set_hash": "sha256:...",
    "evidence": "observed",
    "attested": false
  }
}
```

Rules:

- A remote URL may **link** a repository to a project. It may not **create** or **merge** a project.
- Two different PIDs may not claim the same normalized URL unless an explicit fork relationship is recorded (D9).
- A changed URL (HTTPS→SSH, host migration) updates the repository record after confirmation and preserves the existing link. It does not create a second project.
- A clone whose origin is unknown relative to the registered project is flagged `inherited-identity` and requires confirmation.
- Git root commits and a remote-set hash are corroborating lineage evidence, not proof. They are optional and never fetched automatically.

### 6.5 Registry schema v3 (proposed, additive)

Current canonical schema version is `2`. The migration to `3` must be explicit; the current `_ensure_schema_version` raises on mismatch, so changing the constant without a migration would break existing installs.

Additive columns/tables:

```text
hosts
  + machine_binding TEXT DEFAULT ''
  + binding_strength TEXT DEFAULT 'unknown'    -- strong|weak|none|unknown
  + state_root_id TEXT DEFAULT ''
  + last_seen_at TEXT DEFAULT ''
  + ephemeral INTEGER DEFAULT 0

workspaces
  + binding_generation INTEGER DEFAULT 1
  + identity_hash_at_bind TEXT DEFAULT ''
  + volume_id TEXT DEFAULT ''
  + normalization_version INTEGER DEFAULT 1
  + provenance TEXT DEFAULT 'created'
  + last_verified_at TEXT DEFAULT ''
  + verification_method TEXT DEFAULT ''
  + verification_result TEXT DEFAULT ''

projects
  (no new required column; identity_hash already exists)

new identity_events
  event_id TEXT PRIMARY KEY
  project_id TEXT
  workspace_id TEXT
  host_id TEXT
  event_type TEXT NOT NULL
  at TEXT NOT NULL
  details TEXT DEFAULT '{}'
  prev_hash TEXT DEFAULT ''
  event_hash TEXT NOT NULL

new relocations
  plan_id TEXT PRIMARY KEY
  project_id TEXT NOT NULL
  workspace_id TEXT NOT NULL
  operation TEXT NOT NULL          -- move|copy|clone|restore|adopt
  from_location TEXT
  to_location TEXT
  state TEXT NOT NULL              -- planned|approved|prepared|copied|verified|committed|complete|failed|rolled_back
  journal_hash TEXT
  created_at TEXT NOT NULL
  completed_at TEXT

new project_lineage
  project_id TEXT NOT NULL
  parent_project_id TEXT NOT NULL
  kind TEXT NOT NULL               -- fork|copy|clone|restore|split
  evidence TEXT DEFAULT '{}'
  created_at TEXT NOT NULL
  PRIMARY KEY (project_id, parent_project_id)

new workspace_locations (optional history)
  workspace_id TEXT NOT NULL
  normalized_location TEXT NOT NULL
  volume_id TEXT DEFAULT ''
  valid_from TEXT NOT NULL
  valid_to TEXT
  verification_method TEXT
```

Indexes and constraints:

- `UNIQUE(host_id, normalized_location)` for active workspaces.
- `UNIQUE(normalized_repository_url)` across repositories, unless a recorded fork exception exists.
- `UNIQUE(project_id, parent_project_id)` in `project_lineage`.
- `identity_events` is append-only; updates and deletes are forbidden by the repository layer and audited.

### 6.6 Canonicalization and location rules

Location values must be normalized before comparison:

1. Expand `~`; reject drive-relative paths (`C:foo`) for internal comparison.
2. Make absolute; resolve symlinks and `..`; record whether resolution changed the path.
3. Normalize Unicode to NFC.
4. Remove trailing separators except for a filesystem root.
5. Store the user-facing original in `value`, the comparison form in `normalized`.
6. Add `volume_id` when available:
   - Windows: volume serial or volume GUID path.
   - POSIX: `st_dev` plus filesystem identifier when available.
   - Network shares and some removable media: empty; `binding_strength` degrades.
7. `normalization_version` identifies the algorithm. A future change must not reinterpret old values silently.
8. Equality requires `host_id` + `normalized` + `volume_id` when both volume IDs are present.
9. Case-insensitive comparison is used only on platforms whose filesystem is case-insensitive; the original case is preserved.
10. A path that resolves through a symlink is recorded with both the link path and the resolved path, and the binding is flagged `symlinked`.

Rejected or flagged locations:

- a state root inside a project root;
- a project root that is a symlink without explicit confirmation;
- UNC/network paths for features that require local atomic rename (flag, do not silently fall back);
- read-only or removable media for state writes (state stays on the host);
- cloud-synced directories with conflict files (flag `cloud_synced`).

### 6.7 Invariants

| ID | Invariant |
| --- | --- |
| I1 | A valid identity file has exactly one PID, and PID never changes on move, copy, clone, restore, or reinstall. |
| I2 | PID is never derived from a path, hostname, username, remote URL, or tree hash. |
| I3 | PIH matches the canonical content of the identity file. |
| I4 | HID never travels inside a project and is never derived from the project path. |
| I5 | A workspace belongs to exactly one PID and exactly one HID. |
| I6 | A move retains WID; a copy/clone creates a new WID. |
| I7 | State is never merged between different PIDs. |
| I8 | A missing workspace is marked missing; its state is not deleted. |
| I9 | A remote URL alone never merges two PIDs. |
| I10 | A conflicting identity requires explicit confirmation, fork, or adoption. |
| I11 | Every relocation has a journal entry and an audit event. |
| I12 | Legacy identity files, registry rows, manifests, and state remain readable. |

---

## 7. Lifecycle

### 7.1 Project lifecycle

```text
UNMANAGED
    │ identity create
    ▼
IDENTIFIED (L1)
    │ first workspace bind
    ▼
BOUND (L2)
    │ relocation verified
    ├──────────────► RELOCATED (still BOUND; binding_generation + 1)
    │
    │ explicit fork
    ▼
FORKED (new PID; lineage edge to parent)
    │
    │ archive
    ▼
ARCHIVED (state retained, no active workspace required)
```

Transitions:

| From | Event | To | PID | Requires |
| --- | --- | --- | --- | --- |
| UNMANAGED | `identity create` | IDENTIFIED | new | `--confirm` only if a file exists |
| IDENTIFIED | first bind | BOUND | same | write access to state |
| BOUND | move verified | RELOCATED | same | plan + apply |
| BOUND | fork | FORKED | new | `--confirm`, new identity file |
| BOUND | archive | ARCHIVED | same | explicit |
| ARCHIVED | bind again | BOUND | same | identity still valid |
| any | conflicting identity | CONFLICT | unchanged | owner decision |

### 7.2 Host lifecycle

```text
UNREGISTERED
    │ first run creates host.json
    ▼
REGISTERED (HID created; machine binding recorded)
    │ verify machine binding
    ▼
VERIFIED
    ├── hostname/label change ──► VERIFIED (HID unchanged)
    ├── OS reinstall / machine mismatch ──► FOREIGN (read-only)
    │        │ host adopt --confirm
    │        ▼
    │      VERIFIED (same or new HID, explicitly chosen)
    └── state root copied to another machine ──► FOREIGN (read-only)
```

Rules:

- `host register` becomes idempotent: it reads `host.json` and returns the existing HID; it does not mint a new UUID.
- A state root with a mismatched machine binding is opened read-only. The user must choose `host adopt` (keep HID, accept new machine) or `host new` (new HID, import selected state).
- A host record is never created inside a project.

### 7.3 Workspace lifecycle

```text
PROPOSED (plan)
    │ approve + bind
    ▼
ACTIVE
    ├── verified move on same host ──► ACTIVE (same WID, new location, generation + 1)
    ├── copy/clone ──────────────────► ACTIVE (new WID, same PID)
    ├── location unavailable ────────► MISSING
    │        │ reconnect / rebind
    │        ▼
    │      ACTIVE
    ├── explicit archive ────────────► ARCHIVED
    └── identity conflict ───────────► CONFLICT (quarantined)
```

Rules:

- A workspace is never deleted automatically because a path disappeared.
- `MISSING` retains its last known location and WID.
- A path that later contains a **different** PID is not the same workspace. The old workspace stays `MISSING`; the new one gets a new WID.
- Two active workspaces may share a PID (multiple clones). They do not share a WID.
- A `CONFLICT` workspace cannot be used for writes, report merges, or source deletion until resolved.

### 7.4 Relocation state machine

```text
PLANNED ──approve──► APPROVED ──prepare──► PREPARED ──copy──► COPIED
                                                               │
                                                               ▼
                                                          VERIFIED
                                                               │
                                                          commit
                                                               ▼
                                                          COMMITTED
                                                               │
                                                          finish
                                                               ▼
                                                          COMPLETE

Any state ──failure──► FAILED
FAILED ──resume──► PREPARED | COPIED | VERIFIED
FAILED ──rollback──► ROLLED_BACK
```

Each transition writes the journal before performing the action and records the observed result after it. A transition is idempotent when replayed with the same `plan_id` and journal hash.

---

## 8. Relocation decision matrix

### 8.1 Matrix

Legend: **same** = unchanged; **new** = newly generated; **retain** = keep the existing value; **rebind** = update location on the existing binding; **confirm** = explicit user approval required; **none** = no automatic action.

| # | Scenario | PID/PIH | HID | WID | State | Repository link | Automatic? | Confirmation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Rename directory in place, same volume | same | same | retain | unchanged; reports follow PID | unchanged | yes, if identity valid | no |
| 2 | Move to another directory, same volume | same | same | retain | unchanged | unchanged | yes, via `relocate apply` | no |
| 3 | Move to another volume/drive | same | same | retain | unchanged (state is on host, not project) | unchanged | yes, via `relocate apply`; real-volume test required | yes for `--delete-source` |
| 4 | Copy to USB, same machine | same | same | new | separate workspace history; source history stays | unchanged | plan only | yes |
| 5 | Open USB project on another machine | same | new (that machine) | new | no merge; L1 until bind | unchanged evidence; confirmation if remote unknown | plan only | yes |
| 6 | Copy directory to a second location on the same host | same | same | new | new workspace; project reports aggregate | unchanged | plan only | yes |
| 7 | Copy to another machine (manual/file share) | same | new | new | no merge; import only | unchanged | none | yes |
| 8 | `git clone`, identity file tracked, known remote | same | new | new | new workspace; existing project recognized by PID + repository link | link must match or confirm | after verification | no if PID+remote match |
| 9 | `git clone`, identity tracked, unknown remote | same PID candidate | new | new | quarantined until decision | **do not auto-link** | none | yes |
| 10 | `git clone`, no identity file | none → new | new | new | new provisional project | link is evidence only | none | yes to create/adopt |
| 11 | GitHub/GitLab fork | new PID | new | new | separate from parent | fork exception required | none | yes |
| 12 | Restore backup at original path/host | same if identity valid | same | retain if binding matches; else new | verify before reuse | unchanged | plan only | yes |
| 13 | Restore backup at new path/host | same | same or new host | new | new workspace; no merge | unchanged | plan only | yes |
| 14 | Remote URL change (HTTPS↔SSH, host migration) | same | same | retain | unchanged | update normalized URL, keep repository_id or record alias | after verification | yes |
| 15 | Host rename / hostname change | same | same | retain | unchanged | unchanged | yes | no |
| 16 | OS reinstall on the same machine | same | new or adopted | new | import selected state only | unchanged | none | yes |
| 17 | Move the state directory | same | same | retain | relocate state root; update pointers | unchanged | via `state move` | yes |
| 18 | Share one `OGX_HOME` across machines | same | per host namespace | per host | host-scoped directories; read-only on mismatch | unchanged | detect only | yes |
| 19 | Open from read-only media | same | same | new/missing | state remains local; no writes to media | unchanged | detect only | no |
| 20 | Cloud-synced folder (OneDrive/Dropbox) | same | same | new on conflict | conflict files ignored; generation mismatch flagged | unchanged | detect only | yes to resolve |
| 21 | Container/CI ephemeral host | same | ephemeral HID | new | disposable; no persistence unless exported | unchanged | yes | no |
| 22 | Two processes relocate concurrently | same | same | one wins | journal lock; second refuses | unchanged | no | no |

### 8.2 Rules encoded by the matrix

1. **PID never changes** on scenarios 1–10 and 12–22. Only an explicit fork (11) changes it.
2. **HID changes only when the physical machine/user install changes.** Renaming a host does not change it.
3. **WID is retained only for a move of the same directory.** A copy, clone, restore to a new location, or first open on another host creates a new WID.
4. **State follows PID on the same host.** It does not travel with the project automatically, and it is not merged across hosts.
5. **Repository links survive relocation.** A URL change updates metadata; it does not create a new project.
6. **A clone is not automatically the same project** unless the identity file is valid and the repository link is consistent (or the user explicitly adopts it).
7. **A fork is a new project.** It must not inherit the parent's PID or state.
8. **A missing path is not a deleted project.** The workspace becomes `MISSING`; state is retained.

### 8.3 Forbidden behaviors

The following are explicitly prohibited by this design:

- Deriving PID from a path, tree fingerprint, hostname, or remote URL.
- Treating a remote URL as sufficient proof of project identity.
- Reusing a WID for a different physical copy.
- Moving or deleting state because a path disappeared.
- Merging two `projects.json` files, two registry databases, or two report trees automatically.
- Silently adopting an inherited identity after a clone or a filesystem copy.
- Overwriting a conflicting identity file.
- Writing state inside the project tree.
- Following symlinks during relocation, state copy, or manifest verification.
- Claiming cross-drive/USB/cloud support without a recorded real-media test.

### 8.4 Proposed operations

The following CLI surface is proposed, not implemented:

```text
ogx identity create|show|validate|explain|fork|export|import
ogx identity recover --strategy auto|identity|registry|manifest|new
ogx workspace list|show|bind|verify|forget
ogx relocate plan --from PATH --to PATH [--operation move|copy]
ogx relocate apply --plan ID --confirm [--delete-source]
ogx relocate verify --project PID
ogx host id show
ogx host adopt --confirm
ogx state move --to PATH --confirm
```

Exit codes follow the existing convention: `0` success, `1` verification failure, `2` invalid input/refusal.

---

## 9. State migration sequence

### 9.1 Principles

1. **Identity before state.** Never migrate state until the project identity at the destination is verified.
2. **Plan before write.** Every mutation has a deterministic plan with a digest.
3. **Journal before action.** The journal records the intent and the prior state before the filesystem changes.
4. **Never delete the source first.** The source remains the fallback until the destination is verified and committed.
5. **Idempotent steps.** Replaying a step with the same inputs produces the same result.
6. **No cross-host merge.** Import is an explicit, additive operation with a manifest; it never overwrites a live project.
7. **Legacy is read-only.** Old paths remain readable; new writes go to the new layout only after migration is confirmed.

### 9.2 Move sequence (same host)

| Step | Action | Invariant checked | On failure |
| --- | --- | --- | --- |
| 0 | Preflight: resolve source/destination, reject symlink escapes, verify source identity, check write access, check destination is absent or empty | PID/PIH valid; HID valid; destination does not contain a different PID | refuse; no journal |
| 1 | Acquire relocation lock for `(host_id, project_id, source)` | No other relocation active | refuse with the active plan ID |
| 2 | Write journal `PLANNED` → `APPROVED` with plan digest and source/destination metadata | Journal hash matches the plan | abort |
| 3 | Snapshot source workspace binding, repository links, report index, and identity hash | Source binding exists and is `ACTIVE` | mark `MISSING` only after confirmation |
| 4 | Write journal `PREPARED` | Journal is durable (fsync) | abort |
| 5 | Copy the tree to a temporary sibling on the destination volume | Destination is on a writable local volume; no overwrite | delete temp; journal `FAILED` |
| 6 | Verify destination: read identity, compare PID/PIH, verify key files, reject symlinks | PID/PIH equal; no conflicting identity | keep temp for diagnosis; journal `FAILED` |
| 7 | Atomically rename the temporary directory to the final destination name | Final destination still absent | journal `FAILED`; source untouched |
| 8 | Write journal `COPIED` → `VERIFIED` | Destination identity re-read and equal | journal `FAILED` |
| 9 | Rebind: update workspace location, increment `binding_generation`, set `provenance=moved`, record verification | WID retained; one active binding at destination | rollback binding; journal `FAILED` |
| 10 | Write journal `COMMITTED`; append `identity_event(relocated)` | Audit hash chain valid | journal `FAILED`; reconcile |
| 11 | Mark source binding `MOVED` or `MISSING` (only after commit) | Source location no longer active | leave source active; report |
| 12 | If `--delete-source`, delete the source tree after a second confirmation | Destination committed and verified | never delete; report |
| 13 | Write journal `COMPLETE`; release lock; clean temp | Journal terminal | retain journal for audit |

Notes:

- The tool performs a **copy by default**, not a destructive move. The operating-system rename is an optimization only when source and destination are on the same volume and the final name is free.
- Cross-volume moves are copy + verify + optional source deletion. This is slower but crash-safe.
- Report directories are not copied with the project. They already live under the host state and are re-associated by PID.
- `.git` and `.guardian-x` are copied as ordinary files; the tool never runs Git commands during relocation.

### 9.3 Crash recovery

| Last durable journal state | Recovery action |
| --- | --- |
| none | Nothing happened; safe to plan again. |
| `PLANNED` / `APPROVED` | Discard; no filesystem change. |
| `PREPARED` | Look for a temporary directory. If incomplete, delete it and restart; if complete, continue at verification. |
| `COPIED` | Re-run destination verification; continue or mark failed. |
| `VERIFIED` | Re-run rebind; it is idempotent by `plan_id`. |
| `COMMITTED` | Finish source status, audit, and cleanup; do not repeat the copy. |
| `COMPLETE` | No-op. |
| `FAILED` | Offer `resume` or `rollback`; never overwrite a live destination. |

A journal is only considered committed after its hash chain and the destination identity both verify.

### 9.4 Copy, clone, restore, fork

| Operation | Filesystem behavior | Identity behavior | State behavior |
| --- | --- | --- | --- |
| Move | same tree, new location | PID/PIH same; WID retained | reports follow PID; no copy |
| Copy | duplicate tree | PID/PIH same; WID new | new workspace history; source history retained |
| Clone | new tree from Git | PID only if a valid identity file is present and the remote/link policy passes; otherwise provisional | new workspace; no automatic import |
| Restore | from backup | PID/PIH verified before reuse; WID new unless exact original binding matches | import only if explicitly requested |
| Fork | duplicate tree then new identity | new PID; lineage edge to parent | new project state; parent untouched |
| Adopt | existing tree, no valid identity | explicit new PID or explicit registry adoption | new binding; no merge |

### 9.5 Cross-host import

Cross-host state continuity is **opt-in**:

1. `ogx identity export --project PID --out bundle.ogx.zip --confirm` writes a manifest with PID, PIH, host ID, export time, file list, and SHA-256 for each file.
2. Import on the target host verifies the bundle hash, the PID/PIH, and the absence of a conflicting live project.
3. Import is additive: it creates a new workspace binding and copies reports into that workspace. It never overwrites an existing report with the same ID.
4. A conflict is reported per file; the user chooses `skip`, `rename`, or `abort`.
5. The bundle contains no credentials by default. Secrets are never exported.

### 9.6 Rollback

- Rollback is allowed only before `COMMITTED`.
- Rollback deletes the destination temporary tree or the newly created destination only when it was created by this plan and its identity matches the source.
- Rollback never deletes a pre-existing directory.
- After `COMMITTED`, rollback becomes a new relocation in the opposite direction; it is not a silent undo.
- Every rollback writes an audit event with the original plan ID.

---

## 10. Security threat analysis

### 10.1 Assets

- Project identity file and its integrity.
- Host identity and machine binding.
- Registry integrity (projects, repositories, workspaces, hosts).
- Report and audit history.
- Repository lineage evidence.
- User source code (must never be modified by relocation without explicit intent).
- Credentials (must never enter identity, state, bundles, or logs).

### 10.2 Adversaries and failure sources

- A user accidentally copying a project directory.
- A malicious or compromised project that ships a forged identity file.
- A spoofed or recycled Git remote.
- A shared or copied state directory.
- A symlink or junction inside a project or state path.
- A removable drive reused with a different project.
- A cloud-sync conflict.
- A concurrent process or a crash.
- A malicious manifest or registry row.
- An attacker with local write access to the state root.

### 10.3 Threats and mitigations

| ID | Threat | Impact | Mitigation | Residual risk |
| --- | --- | --- | --- | --- |
| T1 | Identity file copied into an unrelated project | Two unrelated trees share a PID and report history | Detect a PID already bound to a different repository/lineage; require `fork` or `adopt --confirm`; never merge state | A user can still force adoption; audit records it |
| T2 | Identity file edited and hash recomputed | Forged metadata; impersonation of a project | Hash detects accidental corruption only; optional signature/attestation (D2); compare `identity_hash_at_bind`; flag history mismatch | Without a key, authenticity is not provable |
| T3 | Remote URL spoofing / recycled remote | Unrelated repository linked to a project | URL is evidence only; compare PID, lineage, and explicit confirmation; duplicate URL policy | A user can confirm a bad link |
| T4 | Drive letter reused for a different volume | Old workspace resolves to a new project | Record `volume_id`; verify PID at the location; otherwise mark `MISSING` and create a new WID | Volume ID may be unavailable on some media |
| T5 | Casefold collision on Windows/macOS | Two locations compare equal | Store original + normalized; compare case-insensitively only on case-insensitive platforms; use full hash for new keys | Unicode edge cases remain |
| T6 | 48-bit state-key truncation collision | Two projects share a state directory | New layouts use 128-bit keys; write an exact-root marker; detect and refuse collisions; retain legacy keys | Existing collisions require manual repair |
| T7 | Symlink/junction escape | State or manifests read/write outside the intended root | Resolve roots; reject symlinked state/config/manifest paths; `is_relative_to` checks; never follow links during copy | TOCTOU on hostile local filesystems |
| T8 | Shared `OGX_HOME` across machines/users | Host and state mixing | `host.json` machine binding; `shared.json` namespace; read-only on mismatch; per-user default | A copied state root can be forced with `--confirm` |
| T9 | Registry database copied to another machine | Foreign workspace locations appear valid | HID check; foreign workspaces read-only; explicit import/re-bind | A user can clone the whole state |
| T10 | USB/FAT lacks permissions; surprise removal | Partial writes; state corruption | State stays on host; journal on host; atomic replace; fsync; verify after reinsert; never use USB as the only copy | FAT has no ACLs; physical loss |
| T11 | Cloud-sync conflict files | Divergent identity or registry | Ignore conflict-named files; generation numbers; explicit resolution; never pick by mtime | Provider-specific behavior |
| T12 | Stale absolute paths in registry/manifests | False “project missing” or wrong project | Treat locations as hints; verify PID; `MISSING` status; manifest `moved` classification | Old v1 manifests need adoption |
| T13 | TOCTOU during relocation | Wrong tree copied or verified | Journal, lock, re-resolve and re-verify at each step; compare identity after copy | Local attacker with write access |
| T14 | Malicious identity JSON | Oversize, invalid schema, secret leakage, parser abuse | 64 KiB limit, strict schema, UTF-8, secret patterns, no path fields, no executable content | JSON parser memory limits |
| T15 | Malicious manifest | Deletion of user files or path traversal | Manifest v2 PID/PIH check; relative paths only; symlink refusal; SHA-256 ownership; removal only on exact match | A user can confirm a bad manifest |
| T16 | Journal replay or rollback | Duplicate relocation or state rollback | `plan_id` + journal hash + state machine; terminal states immutable; append-only audit | Local attacker can edit files |
| T17 | Host rename or username change | Host misidentification | HID is random; hostname is a label; machine binding is separate | OS reinstall changes binding |
| T18 | Multi-user shared machine | Cross-user state access | Per-user state root; restrictive permissions; do not store identity in a shared project location with secrets | Shared project directories are inherently shared |
| T19 | Ephemeral CI host | Polluted persistent history | `ephemeral` flag; no persistent workspace state by default; export explicitly | CI misconfiguration |
| T20 | Remote/cloud workspace | Local path assumptions fail; no access | Location kind `remote_uri`; no clone by default; state remains per host; capability-gated operations | Provider APIs out of scope |
| T21 | Backup/export leakage | Project metadata or reports exposed | Opt-in export; no credentials; optional redaction; user-controlled destination | User-chosen destination |
| T22 | Registry/report tampering | False history | Append-only audit hash chain; optional signature; integrity check on read | Local admin can rewrite everything |
| T23 | Denial of service via huge trees | Long relocation or hashing | Bounded file count/size for optional verification; progress and cancellation; never hash unbounded data by default | Very large repositories |
| T24 | Windows short paths / long paths | Identity mismatch or failed copy | Resolve canonical paths; long-path-aware APIs; record both forms; test | Legacy APIs |

### 10.4 Trust-boundary statement

`identity_hash` provides **integrity against accidental change**, not **authenticity**. The design must say so in user-facing output. A keyed or signed attestation is the only way to raise assurance, and it requires key management that is deliberately out of scope for the first implementation phase.

---

## 11. Canonical/drop-in parity

### 11.1 Current divergence

| Concern | Canonical `src/ogx` | Drop-in `core/ogx` + `init.py` | Divergence risk |
| --- | --- | --- | --- |
| Identity file | Yes, schema 1.0 | None | Drop-in cannot preserve PID |
| Identity hash | `sha256:` canonical JSON | None | No parity |
| Host record | Registry `hosts` table; random per register | None | Host identity absent |
| Workspace record | Registry `workspaces` table with location fields | State directory derived from absolute path | Different meaning of “workspace” |
| State key | User state root + path hash in `projects.json` | `sha256(casefold(HOST_ROOT))[:12]` | Both path-dependent, differently |
| Registry | SQLite schema v2 | `projects.json` only | No shared query model |
| Link manifest | `OMEGA GUARDIAN X/integration-manifest.json`, absolute root | Shield-folder manifest, absolute root | Different paths, same defect |
| CLI identity | `identity create/show/validate/recover/update` | None | Feature gap |
| CLI host | `host ...` | None | Feature gap |
| Relocation verification | `assert_relocation_safe` (shallow) | None | No parity |
| Recovery evidence | API accepts registry/manifest; CLI does not pass it | None | Behavior gap |

### 11.2 Parity contract

Both distributions must agree on the following observable behavior:

1. **Identity schema and canonicalization** — the same JSON produces the same PIH.
2. **UUID and timestamp validation** — the same invalid input is rejected with the same category of error.
3. **Identity authorization** — an existing file is never overwritten without explicit confirmation.
4. **Resolution precedence** — identity > registry > explicit > manifest evidence > location hint > fingerprint; remote URL never primary.
5. **Conflict behavior** — conflicting identity is quarantined; no silent merge or overwrite.
6. **Workspace semantics** — move retains WID; copy/clone creates a new WID; missing is not deleted.
7. **State semantics** — state is per host, keyed by PID; no automatic cross-host merge.
8. **Manifest behavior** — v2 fields and v1 compatibility classification are identical.
9. **Relocation invariants** — the same plan/apply/verify steps and the same refusal conditions.
10. **Audit events** — the same event names and required fields for identity/relocation actions.
11. **CLI subset** — the same verb names and exit codes for the common commands.
12. **No secrets** — neither distribution writes credentials into identity, registry, state, manifests, logs, or exports.

Storage technology is explicitly **not** part of the parity contract:

- Canonical uses SQLite Registry V2/V3.
- Drop-in should use atomic JSON indexes (standard library only; no new dependency).
- Both expose the same logical records.

### 11.3 Proposed drop-in module layout

```text
core/ogx/kernel/
├─ identity.py            # create/read/validate/hash; schema 1.0; stdlib only
├─ host_identity.py       # host.json, machine binding (best effort)
├─ workspace_state.py     # atomic JSON index, lock, relocation journal
└─ state_layout.py        # legacy and v2 paths, collision markers

core/ogx/cli/commands/
└─ identity.py            # identity/workspace/relocate/host-id commands
```

Why `kernel`: the current architecture test allows `kernel` to import nothing, which is exactly right for a standard-library-only identity primitive. If a separate package is preferred, the architecture test must be updated deliberately; it must not be bypassed with local imports.

`init.py` additions:

- resolve `host.json` from the state root;
- expose `identity`, `workspace`, and `relocate` aliases;
- keep `OMEGA_HOST_ROOT`, `OGX_HOME`, `OMEGA_SHIELD_HOME`, and `OMEGA_SHIELD_DIRNAME` semantics;
- never write inside the host project except through the existing explicit link commands.

### 11.4 Shared golden vectors

Create `_dev/fixtures/identity_vectors.json` with:

- minimal valid identity;
- maximal valid identity (description at limit);
- each invalid field (UUID version, missing timezone, bad timestamp, oversize, unknown field, secret-like value);
- canonical JSON bytes and expected PIH for each valid case;
- expected error category for each invalid case;
- relocation scenarios expressed as abstract records (PID/PIH/HID/WID transitions), independent of storage.

Both test suites load the same vectors. A parity test fails if either implementation diverges.

### 11.5 Remaining intentional differences

| Difference | Reason | Acceptable? |
| --- | --- | --- |
| SQLite vs JSON storage | Canonical registry already exists; core is stdlib-only and has no registry DB | Yes, if logical records and behavior match |
| Provider engine absent in drop-in | Documented distribution boundary | Yes |
| Live host sessions absent in drop-in | Process-local feature of canonical | Yes, must be documented |
| Recovery UI depth | Drop-in has a smaller CLI | Yes, if core safety behavior matches |
| Audit retention policy | Configurable per distribution | Yes, if append-only semantics match |

### 11.6 Parity gates

- `python _dev/tools/sync_core_from_src.py --check` remains green.
- `python -m pytest -q` (canonical) and `PYTHONPATH=core python -m unittest discover -s _dev/tests -v` (drop-in) both pass.
- Golden vectors pass in both.
- A scripted relocation fixture produces the same PID/PIH/WID transitions in both.
- No canonical-only field is required to read a drop-in identity file, and vice versa.

---

## 12. Backward compatibility

### 12.1 Compatibility matrix

| Component | Current format | Proposed change | Old reader behavior | New reader behavior | Migration |
| --- | --- | --- | --- | --- | --- |
| `identity.json` | Schema 1.0, strict fields | No in-place field additions in phase 1 | Unchanged | Accepts 1.0; unchanged validation | None required |
| Identity hash | `sha256:` canonical JSON | Unchanged | Unchanged | Unchanged | None |
| Registry DB | Schema version 2 | Additive schema 3 | Fails closed on version mismatch (current behavior) | Migrates 2→3 after backup; opens 3 | `registry migrate --to 3 --confirm` |
| `projects.json` | Name → absolute path | Keep read-only; registry becomes authoritative | Unchanged | Reads legacy; never rewrites automatically | Optional import |
| Report directories | `<name>-<pathhash>/reports` | PID/WID layout when layout v2 is enabled | Unchanged | Reads legacy; indexes both; writes v2 only after opt-in | `state migrate --to 2 --confirm` |
| Link manifest v1 | Absolute `project_root` | Manifest v2 with PID/PIH/root hash | Unchanged | Reads v1; classifies `moved`; requires adoption to rewrite | `link adopt --confirm` |
| Drop-in state dir | `<slug>-<sha256(path)[:12]>` | Host/PID/WID layout under the state root | Unchanged | Reads legacy; new writes v2 after opt-in | `state migrate --to 2 --confirm` |
| Host records | Random UUID per register | Stable `host.json` | Unchanged | Reuses existing HID; registers idempotently | Automatic on first run |
| Host CLI | `host register` creates a row | Same command, idempotent | Unchanged | Returns existing HID | None |
| `identity recover` | Creates new identity with `--confirm` | Evidence-aware strategy | Unchanged CLI shape | `--strategy auto` default; refuses silent new ID when evidence exists | Documented behavior change |
| Drop-in CLI | `init.py` aliases only | Adds identity/workspace/relocate aliases | Unchanged | Additive | None |

### 12.2 Identity schema compatibility

The schema is strict. The design therefore **does not add fields to `identity.json`**. New metadata lives in:

- `host.json` (host-scoped, outside the project);
- registry columns and tables;
- optional sidecars with their own schema and version;
- manifest v2.

If a future project-identity extension is required, it must be a new schema version (`1.1` or `2.0`) with a dual reader. A v1.0 binary must be able to reject the new version explicitly rather than misread it.

### 12.3 Registry migration rules

1. Read `schema_version` before opening any table.
2. If version is `2`, require `--confirm`, write a backup beside the database, and apply additive migrations in one transaction.
3. If version is `3`, open normally.
4. If version is greater than `3`, fail closed with an actionable message.
5. If version is missing or corrupt, refuse; never guess.
6. Migration is idempotent: re-running on a v3 database is a no-op.
7. A dry-run reports the row counts and the planned changes without writing.
8. The backup is never deleted automatically.

### 12.4 Manifest compatibility

Reader rules:

- **v1 manifest, root matches:** treat as valid legacy.
- **v1 manifest, root differs, PID/PIH match:** classify `moved`; allow read-only status; require `link adopt --confirm` before any write or removal.
- **v1 manifest, root differs, no identity:** classify `foreign`; refuse write/removal; print the recorded root and the detected root.
- **v2 manifest, PID/PIH match:** valid regardless of path.
- **v2 manifest, PID/PIH mismatch:** refuse; never “repair” automatically.
- **Any manifest with traversal, symlink, or unknown structure:** refuse.

Writer rules:

- New writes use v2 only after explicit adoption or a new integration.
- v1 files are never rewritten in place without `--confirm`.
- Removal still requires matching SHA-256 for every managed file.

### 12.5 State layout compatibility

- Legacy state is read-only until `state migrate --to 2 --confirm`.
- The migration copies or links only metadata; reports are moved with a journal and hash verification.
- A legacy report directory without identity evidence remains associated with its legacy path and is marked `legacy-unbound`.
- No legacy file is deleted by migration.
- If a state root is shared across hosts, v2 namespacing is mandatory; legacy root-level data is imported into the current host namespace with an audit record.

### 12.6 CLI compatibility

- Existing commands and flags remain valid.
- New flags are additive.
- Default behavior changes only where safety requires it, and each change is listed in `CHANGELOG.md` with a migration note.
- The `identity recover` change is the only planned default-behavior change: evidence is now consulted before creating a new identity. The command still requires `--confirm` when it would create or replace anything.
- Machine-readable `--json` output is versioned (`schema_version` field) when the shape changes.

### 12.7 Compatibility test gates

- Open a v1 identity file with the new reader: unchanged PID/PIH.
- Open a v2 registry with the new code: migration succeeds after backup; v1 data preserved.
- Open a v1 manifest after a move: `moved`, not a crash.
- Read a legacy report directory: still listed; no rewrite.
- Run the old CLI path (aliases) after the new modules are added: unchanged output for unchanged commands.
- Run the drop-in suite with `PYTHONPATH=core`: legacy state still readable.

## 13. Required regression test matrix

### 13.1 Test layers

| Layer | Purpose | Runs where |
| --- | --- | --- |
| L-A Unit | Validation, canonicalization, normalization, state machine transitions | Canonical and drop-in suites; no filesystem media requirement |
| L-B Integration | Registry, manifest, state layout, CLI verbs against a temporary directory | Canonical suite |
| L-C Parity | Same golden vectors and abstract relocation records through both distributions | Canonical + drop-in, shared fixtures |
| L-D Crash/transaction | Journal replay, interrupted copy, lock contention | Simulated: injected failure points, no real crash required |
| L-E Real media | Cross-drive, USB removal/reinsertion, second machine, real remote | Manual evidence run sheet on physical hardware |

L-A through L-D are automatable and must be green before a phase is accepted. L-E cannot be claimed by any automated suite and must never be written up as passing until a dated run sheet with real volumes exists.

### 13.2 Matrix

Status meaning: **EXISTS** = a test covers this behavior at `bfa5db6` and passed in the focused run recorded in §16; **NEW** = required by this design and not present today; **REAL MEDIA** = requires L-E evidence.

#### Identity

| ID | Scenario | Layer | Asserts | Status |
| --- | --- | --- | --- | --- |
| RT-01 | Create minimal identity | L-A | One PID, UUIDv4, canonical JSON, hash verifies | EXISTS |
| RT-02 | Create with explicit UUID | L-A | Supplied UUIDv4 accepted; other versions rejected | EXISTS |
| RT-03 | Refuse overwrite without `--confirm` | L-A | Existing file unchanged, non-zero exit | EXISTS |
| RT-04 | Overwrite with `--confirm` | L-A | New identity written; old hash not reused | EXISTS |
| RT-05 | Reject different UUID on overwrite | L-A | PID cannot be swapped through create | EXISTS |
| RT-06 | Hash mismatch on read | L-A | `IdentityCorruptedError`; no silent repair | EXISTS |
| RT-07 | Unknown field / oversize / secret-like content | L-A | Rejected with the same category in both distributions | EXISTS (canonical), NEW (drop-in) |
| RT-08 | `identity update` immutable fields | L-A | Only `project_name`, `description` change; hash recomputed; PID stable | EXISTS |
| RT-09 | `identity recover` with registry evidence | L-B | Recovers the registry PID; does not mint a new one | NEW |
| RT-10 | `identity recover` with manifest evidence only | L-B | PID treated as supporting evidence; confirmation required | NEW |
| RT-11 | `identity recover` with no evidence | L-B | Refuses without `--confirm`; creates provisional identity only with it | NEW |
| RT-12 | `identity explain` | L-B | Reports each evidence source and why it lost | NEW |
| RT-13 | `identity fork` | L-B | New PID, lineage edge, parent untouched, no state copy | NEW |
| RT-14 | Identity survives directory rename | L-B | PID/PIH identical before and after | EXISTS (`test_identity_is_preserved_and_survives_relocation`) |

#### Host

| ID | Scenario | Layer | Asserts | Status |
| --- | --- | --- | --- | --- |
| RT-15 | First run creates `host.json` | L-A | UUIDv4 HID, schema, binding fields | NEW |
| RT-16 | `host register` twice | L-B | Same HID both times; no second row | NEW |
| RT-17 | Hostname/label change | L-A | HID unchanged; label updated | NEW |
| RT-18 | Machine binding mismatch | L-B | State root opened read-only; `foreign` reported | NEW |
| RT-19 | Missing machine identifier | L-A | `binding_strength` degrades to `weak`; no crash | NEW |
| RT-20 | Two users, one machine | L-B | Separate state roots; no cross-read | NEW |
| RT-21 | Ephemeral/CI host | L-B | No persistent workspace history unless exported | NEW |

#### Workspace and relocation

| ID | Scenario | Layer | Asserts | Status |
| --- | --- | --- | --- | --- |
| RT-22 | First bind | L-B | WID created; one active binding per location | NEW |
| RT-23 | Move on same volume | L-B | Same WID, same PID, generation + 1, one active binding | NEW |
| RT-24 | Copy to a second directory | L-B | Same PID, new WID, source binding untouched | NEW |
| RT-25 | Copy with state present | L-B | No report merge; no WID reuse | NEW |
| RT-26 | Missing location | L-B | Workspace `MISSING`, state retained, no deletion | NEW |
| RT-27 | Location reused by a different PID | L-B | Old WID stays `MISSING`; new WID for the new PID | NEW |
| RT-28 | Symlinked location | L-B | Flagged `symlinked`; no traversal outside the root | NEW |
| RT-29 | State root inside project | L-B | Refused (existing behavior preserved) | EXISTS |
| RT-30 | Registry workspace location update | L-B | Location updated, PID unchanged | EXISTS (`test_update_workspace_location`) |
| RT-31 | Concurrent relocation plans | L-D | Second plan refused with the active plan ID | NEW |
| RT-32 | Interrupted copy at each journal state | L-D | Resume or rollback per §9.3; no live destination overwritten | NEW |
| RT-33 | `--delete-source` without second confirmation | L-B | Refused; source intact | NEW |
| RT-34 | Destination contains a different PID | L-B | Refused before any write | NEW |
| RT-35 | Cross-drive move C: to D: | L-E | Copy + verify + rebind; source retained unless confirmed deleted | REAL MEDIA |
| RT-36 | USB removal mid-copy | L-E | Journal `FAILED`; host state intact; resume after reinsert | REAL MEDIA |
| RT-37 | USB opened on a second machine | L-E | New HID, new WID, no state merge, L1 only | REAL MEDIA |
| RT-38 | Project opened from read-only media | L-E | Detected; no writes to media; state stays local | REAL MEDIA |

#### Manifest and link integration

| ID | Scenario | Layer | Asserts | Status |
| --- | --- | --- | --- | --- |
| RT-39 | v1 manifest, matching root | L-B | Valid legacy; unchanged behavior | EXISTS |
| RT-40 | v1 manifest after a move | L-B | Classified `moved`; read-only status; no hard failure | NEW |
| RT-41 | v1 manifest, foreign root, no identity | L-B | `foreign`; write/removal refused | NEW |
| RT-42 | v2 manifest PID/PIH match at a new path | L-B | Accepted; no adoption prompt | NEW |
| RT-43 | v2 manifest PID mismatch | L-B | Refused; no automatic repair | NEW |
| RT-44 | Manifest path traversal / symlink | L-B | Refused; removal never follows links | EXISTS (symlink cases skip on this host) |
| RT-45 | Removal requires exact SHA-256 | L-B | Modified or missing managed file blocks removal | EXISTS |

#### State, registry, and cross-host

| ID | Scenario | Layer | Asserts | Status |
| --- | --- | --- | --- | --- |
| RT-46 | v1 `projects.json` read | L-B | Read-only; never rewritten automatically | NEW |
| RT-47 | Report directory follows PID after a move | L-B | Same report history at the new location | NEW |
| RT-48 | 128-bit state key collision marker | L-A | Collision detected; exact-root check refuses reuse | NEW |
| RT-49 | Legacy 48-bit key still readable | L-B | Legacy directories listed; no rewrite | NEW |
| RT-50 | Registry v2 → v3 migration | L-B | Backup written, additive migration, idempotent re-run | NEW |
| RT-51 | Registry version 4+ | L-B | Fails closed with an actionable message | NEW |
| RT-52 | v1 → v2 registry migration without identity | L-B | Provisional deterministic ID + `provisional` marker, not a bare random UUID | NEW |
| RT-53 | Export bundle then import on a clean state root | L-B | PID/PIH preserved; additive; no overwrite | NEW |
| RT-54 | Import with a conflicting live project | L-B | Refused; per-file conflict report | NEW |
| RT-55 | Two projects, same folder name | L-B | Distinct PIDs, distinct state, no collision | NEW |
| RT-56 | Two PIDs claim one normalized repository URL | L-B | Second claim refused unless a recorded fork exception exists | NEW |
| RT-57 | URL change HTTPS → SSH | L-B | Same PID; repository row updated after confirmation | NEW |
| RT-58 | Clone with tracked identity, matching remote | L-E | Recognized as the same project; new host/WID | REAL MEDIA |
| RT-59 | Clone with tracked identity, unknown remote | L-B | Quarantined; no auto-link | NEW |
| RT-60 | Clone without identity | L-B | Provisional project; explicit create/adopt | NEW |

### 13.3 Golden vectors and parity

`_dev/fixtures/identity_vectors.json` is the single source of truth for both distributions. Each vector carries: input bytes, expected canonical bytes, expected `identity_hash`, expected accept/reject, and expected error category. Required cases:

- minimal valid identity;
- maximal valid identity (description at the limit);
- Unicode project name and description;
- UUIDv1/v5 and malformed UUID;
- missing or non-UTC timestamp;
- unknown field;
- file above 64 KiB;
- credential-like value;
- hash mismatch;
- CRLF vs LF input (canonicalization must not depend on line endings).

Parity is asserted at the level of observable behavior, not storage: same accept/reject, same PID/PIH, same conflict classification. A parity failure blocks every phase after P0.

### 13.4 Tests that must stay green

- Canonical focused suites: `tests/test_identity.py`, `tests/test_registry.py`, `tests/test_intelligence_integration.py`, `tests/test_manifest.py`, `tests/test_integration.py`.
- Canonical full suite and drop-in `_dev` suite.
- `python _dev/tools/sync_core_from_src.py --check`.
- `_dev/tests/test_architecture.py` (kernel import rule).

### 13.5 What the matrix deliberately does not assert

- That a copy is safe to delete from the source. That is a user decision, never a test outcome.
- That a remote repository is authentic. Only that a URL is consistent or inconsistent with recorded evidence.
- That a journal survived an actual power loss. Injected failure points approximate, and the design must say so.
- That USB behavior is verified. Without RT-35 through RT-38 evidence it remains **UNVERIFIED**.

---

## 14. Phased implementation plan

Each phase is independently reviewable, keeps the previous behavior available, and must not start before its predecessor's exit gate passes. No phase ships a default-behavior change without a `CHANGELOG.md` entry and a migration note.

| Phase | Scope | Primary touchpoints | Required tests | Exit gate |
| --- | --- | --- | --- | --- |
| P0 | Harness only: golden vectors, parity runner, relocation fixtures. No behavior change. | `_dev/fixtures/`, `_dev/tests/`, `tests/` | Vectors in both distributions; sync check | Both suites green; no schema or CLI change |
| P1 | Identity evidence and recovery: wire registry/manifest evidence into `identity recover`; add `explain` and `fork`; deterministic provisional IDs in v1 migration. | `src/ogx/pap/identity.py`, `src/ogx/cli.py`, `src/ogx/registry/v2.py` | RT-09…RT-14, RT-52 | Recovery never mints a new PID when evidence exists; migration is idempotent |
| P2 | Host identity: `host.json`, idempotent registration, machine binding, foreign read-only. | `src/ogx/hosts/`, `src/ogx/cli.py`, new `src/ogx/state/host.py` | RT-15…RT-21 | Re-registering returns the same HID; mismatch is read-only |
| P3 | Workspace binding and manifest v2: WID, generation, provenance, v1 `moved` classification, `link adopt`. | `src/ogx/registry/v2.py`, `src/ogx/project_integration.py`, `core/ogx/ecosystem/integration.py` | RT-22…RT-30, RT-39…RT-45 | A moved legacy manifest is `moved`, not an error; no WID reuse on copy |
| P4 | Relocation protocol: plan/apply/verify, journal, lock, cross-volume copy, rollback, consent for source deletion. | new `src/ogx/relocation/`, `src/ogx/cli.py` | RT-31…RT-34, RT-35…RT-38 (real media) | Journal states reproducible; no destination overwrite; real-media sheet recorded |
| P5 | State layout v2 and registry v3: PID-keyed directories, 128-bit keys, collision markers, migration command, legacy read-only. | `src/ogx/registry/v2.py`, `core/ogx/cli/common.py`, new `src/ogx/state/layout.py`, `init.py` | RT-46…RT-51 | Migration is opt-in, backed up, idempotent, and legacy remains readable |
| P6 | Cross-host export/import, remote/cloud location kinds, ephemeral hosts. | new `src/ogx/state/export.py`, `src/ogx/cli.py` | RT-53…RT-60 | Import is additive and conflict-safe; no automatic clone or merge |
| — | Drop-in parity for every phase | `core/ogx/kernel/*`, `init.py`, `_dev/tests` | Shared vectors plus the phase's drop-in subset | Parity gate green before the phase is accepted |

### 14.1 Sequencing rules

1. P0 lands first and changes nothing observable. Without it, the two implementations cannot be proven equivalent.
2. P1 and P2 are prerequisites for P4: a relocation needs a stable PID and a stable HID to rebind against.
3. P3 before P4: the relocation protocol updates a workspace binding that must already exist.
4. P5 after P4: state layout changes are safer once relocation is journaled, because the same journal moves report metadata.
5. P6 last: cross-host import depends on PID-keyed state.
6. Drop-in parity for a phase ships in the same change set as the canonical behavior, or the phase is not accepted.

### 14.2 Rollback per phase

| Phase | Reversible by | Data risk |
| --- | --- | --- |
| P0 | Deleting fixtures and the parity test | None |
| P1 | Reverting the CLI wiring; identity files unchanged | Low |
| P2 | Deleting `host.json`; registry rows untouched | Low |
| P3 | Keeping manifest v1 writes; v2 rows unused | Medium (dual-write window) |
| P4 | Journals remain readable; no automatic migration | Medium |
| P5 | Legacy layout untouched until explicit migration; backup retained | High if migration is forced |
| P6 | Export files are inert; no automatic import | Low |

---

## 15. Risks, trade-offs, and unresolved decisions

### 15.1 Risks

| ID | Risk | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- | --- |
| R1 | Canonical and drop-in implementations drift | High | Identity split across distributions | Shared vectors, parity gate in every phase, sync check |
| R2 | Registry v3 or state migration loses data | Medium | History loss | Backup, opt-in, transactional migration, legacy read-only |
| R3 | Users read `identity_hash` as proof of authenticity | High | False trust | Explicit wording in CLI, docs, and reports; D2 |
| R4 | A copied project silently shares a PID | High | Two trees write one history | Copy → new WID; conflict detection; confirmation; never delete state |
| R5 | Machine binding unavailable (VM, container, renamed OS) | Medium | Host identity degrades | `binding_strength=none`; explicit `host adopt`; never guess |
| R6 | Cross-volume copy is slow or partial on very large trees | Medium | Timeout, disk exhaustion | Bounded progress, free-space preflight, resumable journal |
| R7 | Path normalization edge cases (UNC, 8.3 names, junctions, NFC) | Medium | Two bindings for one location, or a false match | Single normalization module, versioned algorithm, both forms stored |
| R8 | Users confuse copy with move | High | Orphaned state or duplicate history | Default copy semantics, explicit `--delete-source`, printed summary |
| R9 | Journal corrupt or replayed | Low | Duplicate relocation | Hash chain, terminal states, plan lock, audit event |
| R10 | Cloud-sync conflict files | Medium | Divergent identity or registry | Ignore conflict names, generation checks, explicit resolution |
| R11 | Resolution path becomes slower (extra hashing) | Medium | CLI latency | Fingerprint stays bounded and sampled; identity read is O(1) |
| R12 | Real-media tests are skipped and support is overclaimed | High | False compatibility claim | L-E run sheet required; report mark NOT RUN until it exists |

### 15.2 Trade-offs accepted by this design

1. **Identity stays a strict schema 1.0 file.** Adding fields would break every existing reader; host, workspace, and lineage metadata therefore live outside the project. Cost: more files and a lookup step. Benefit: no schema migration for the one artifact that must survive every move.
2. **State is host-local, not project-local.** A project carried on USB keeps its identity but not its history. Cost: explicit export/import. Benefit: no state directory inside a repository, and no accidental sharing through a copy.
3. **Move is copy + verify, not rename.** Rename is used only as an optimization on the same volume with a free destination. Cost: time and temporary disk. Benefit: crash safety and a verified destination before the source is touched.
4. **Confirmation over automation.** Copy, adoption, fork, URL change, and source deletion all require an explicit decision. Cost: extra prompts. Benefit: no silent merge, which is the failure mode that destroys trust.
5. **SQLite for canonical, JSON for drop-in.** Cost: two storage code paths. Benefit: the drop-in stays standard-library-only, and the parity contract is behavioral rather than structural.
6. **`volume_id` is best-effort.** Cost: weaker detection on network and some removable media. Benefit: no dependency on platform-specific APIs that fail silently.

### 15.3 Unresolved decisions requiring owner approval

Each decision names a recommended default. Nothing in this document authorizes implementation of a decision marked DECISION REQUIRED.

| ID | Decision | Options | Recommendation | Blocks |
| --- | --- | --- | --- | --- |
| D1 | Where host identity lives | (a) `<state>/host.json` (b) registry `hosts` row only (c) both | (a) as authority, registry mirrors it | P2 |
| D2 | Authenticity of project identity | (a) hash only, documented as integrity (b) optional keyed attestation (c) mandatory signing | (a) now, (b) as a later opt-in; (c) rejected as disproportionate | P1 wording, P6 |
| D3 | State layout v2 activation | (a) opt-in migration (b) automatic on first run (c) new installs only | (a); never rewrite a live state root automatically | P5 |
| D4 | PID on a filesystem copy | (a) same PID, new WID (b) automatic fork (c) prompt at first open | (a) as default with a conflict check; (c) when the same PID is already bound on the host | P1, P3 |
| D5 | WID on same-volume rename | (a) retain (b) always new | (a); a rename is not a new copy | P3 |
| D6 | Registry schema v3 timing | (a) with P3 (b) with P5 (c) later | (b); P3 can use existing columns plus a sidecar | P3, P5 |
| D7 | Drop-in storage | (a) atomic JSON indexes (b) SQLite (c) both | (a); no new dependency, parity is behavioral | P0, P2 |
| D8 | Accepted machine-binding sources | (a) OS machine ID only (b) OS ID plus state-root ID (c) any stable hardware ID | (b); (c) is brittle and privacy-sensitive | P2 |
| D9 | Two PIDs claiming one repository URL | (a) refuse (b) allow with recorded fork exception (c) allow silently | (a) default, (b) only with an explicit `fork` record | P3, P6 |
| D10 | Repository URL change | (a) require confirmation (b) auto-update when the host matches (c) refuse | (a); a URL change is a lineage edit | P3 |
| D11 | Default relocation semantics | (a) copy, source retained (b) move, source deleted (c) ask | (a); deletion is always a second explicit step | P4 |
| D12 | Cross-host import contents | (a) reports and audit only (b) also registry rows (c) full state | (a) first; (b) only through explicit adoption of a workspace | P6 |

### 15.4 Open questions that are not design blockers

- Whether `ogx workspace` belongs in the drop-in CLI or only in canonical. Recommended: read-only `workspace list/show` in drop-in, full verbs in canonical (D7 does not change this).
- Whether a relocation journal should be mirrored into the project as a sidecar. Recommended: no; it belongs to host state.
- Whether `identity fork` should copy reports. Recommended: no; fork creates identity and lineage only.
- Retention period for `relocations/` journals. Recommended: keep until the next successful relocation of the same project, then archive.

---

## 16. Verification and confirmations

### 16.1 Repository state

| Item | Value |
| --- | --- |
| Repository root | `D:\FTP\OMEGA-GUARDIAN-X-Universal` |
| Product root | `D:\FTP\OMEGA-GUARDIAN-X-Universal\GUARDIAN-X` |
| Deliverable path | `D:\FTP\OMEGA-GUARDIAN-X-Universal\GUARDIAN-X\docs\UNIVERSAL_RELOCATION_IDENTITY_DESIGN.md` |
| HEAD | `bfa5db6f52891ded0d7602a87efede286aad7927` |
| Branch | `main` |
| `origin/main` | `https://github.com/shajeduzzamanenan992008inc/OMEGA-GUARDIAN-X-Universal.git` |
| Working tree at milestone start | Clean; this document did not exist |
| Working tree at resume | Clean except an uncommitted draft of this document (untracked), completed in place rather than replaced |
| Working tree after | One untracked file: this document. No modification, staging, commit, branch, merge, or push. |
| Python used for the focused run | CPython 3.15.2, Windows 10.0.19045 |

### 16.2 Commands executed

```text
git rev-parse HEAD                 -> bfa5db6f52891ded0d7602a87efede286aad7927
git rev-parse --abbrev-ref HEAD    -> main
git status --porcelain=v1          -> empty at milestone start; the draft only at resume; one untracked file after
python -m pytest tests/test_identity.py tests/test_registry.py \
    tests/test_intelligence_integration.py tests/test_manifest.py \
    tests/test_integration.py -q   -> 124 passed, 2 skipped (12.90 s)
```

The two skips are explicit platform skips for symlink tests on this Windows host, not failures. No source, test, configuration, packaging, or license file was executed with side effects; the test run used temporary directories only.

### 16.3 Files inspected for this design

| Path | Lines | Used for |
| --- | --- | --- |
| `src/ogx/pap/identity.py` | 532 | Identity create/read/hash/recover contract |
| `src/ogx/pap/identity_schema.py` | 243 | Strict schema, 64 KiB limit, secret patterns |
| `src/ogx/pap/manifest_engine.py` | 541 | Manifest lifecycle |
| `src/ogx/pap/manifest_schema.py` | 366 | Manifest fields |
| `src/ogx/registry/v2.py` | 1163 | Schema v2, workspace rows, `migrate_from_v1` |
| `src/ogx/registry/models.py` | 83 | Row models |
| `src/ogx/workspace.py` | 343 | Path-dependent fallback fingerprint |
| `src/ogx/intelligence/integration.py` | 151 | Resolution precedence |
| `src/ogx/intelligence/engine.py` | 189 | Analysis entry |
| `src/ogx/platform/identity.py` | — | URL normalization, repository linking, `assert_relocation_safe` |
| `src/ogx/project_integration.py` | 339 | Absolute `project_root`, manifest refusal |
| `src/ogx/hosts/registry.py` | — | Host rows, per-register `uuid4()` |
| `src/ogx/cli.py` | — | `identity`, `host`, `projects` verbs; recover wiring |
| `init.py` | 239 | Drop-in state home, `_prepare_env`, aliases |
| `core/ogx/ecosystem/integration.py` | — | Drop-in manifest root check |
| `core/ogx/cli/common.py` | — | Path-keyed external report directory |
| `_dev/tests/test_architecture.py` | — | `kernel` imports nothing |
| `tests/test_identity.py` | — | Identity coverage |
| `tests/test_registry.py` | — | Workspace update coverage |
| `tests/test_intelligence_integration.py` | — | Relocation preservation test |
| `tests/test_manifest.py` | — | Manifest coverage |
| `tests/test_integration.py` | — | Link integration coverage |

### 16.4 Confirmations against the milestone constraints

| Constraint | Result |
| --- | --- |
| Single agent, no subagents | Satisfied |
| Repository and HEAD verified before work | Satisfied (§16.1) |
| Existing user changes preserved | Satisfied; no tracked file touched |
| Source code inspected rather than relying on reports | Satisfied; every gap in §3 cites a module read at this HEAD |
| Read-only investigation | Satisfied; the only write is this document |
| No implementation | Satisfied; all new behavior is marked PROPOSED |
| No source/test/config/packaging edit | Satisfied |
| No stage/commit/push/merge/branch | Satisfied |
| LICENSE and license metadata untouched | Satisfied |
| No destructive operation | Satisfied |
| Cross-drive/USB/remote not claimed as verified | Satisfied; RT-35…RT-38, RT-58 are REAL MEDIA / not run |
| Deliverable at the required absolute path | Satisfied (§16.1) |

### 16.5 Explicitly not run

- No real cross-drive relocation (C: → D:).
- No USB removal/reinsertion, and no second physical machine.
- No GitHub/GitLab clone round-trip, no cloud workspace, no network call of any kind.
- No macOS, Linux, or Python 3.11–3.13 execution.
- No crash-injection or power-loss simulation.
- No migration of a live registry, state root, manifest, or report tree.

These remain **UNVERIFIED** and are the first items an implementation milestone must convert into recorded evidence.

### 16.6 Final state

Only one file changed in the repository: this document, added and left untracked. HEAD remains `bfa5db6f52891ded0d7602a87efede286aad7927`, branch `main`, no staged changes, no commits, no remote operations. Implementation has not started.
