# Host Contract Verification (Phase 3)

Date: 2026-11-24
Suite: `tests/test_host_contract.py` — **12 tests, 12 passed**.

## 1. What the suite covers

| Contract area | Test |
| --- | --- |
| Registration | `test_registration_and_retrieval` |
| Duplicate registration | `test_duplicate_registration_updates_without_duplicating` |
| Missing host | `test_missing_host_raises` |
| Invalid configuration | `test_invalid_host_id_fails_explicitly` |
| Unknown host type handling | `test_unknown_host_type_is_not_silently_trusted` |
| Listing/filtering/pagination | `test_listing_filters_and_pagination` |
| Status lifecycle and soft delete | `test_status_lifecycle_and_soft_delete` |
| Persistence across reload | `test_registry_persistence_survives_reload` |
| Project↔host linking | `test_project_host_links` |
| Capability reporting | `test_capability_reporting_is_consistent` |
| Local filesystem adapter metadata/access | `test_local_filesystem_adapter_contract` |
| Local Git adapter metadata/detection | `test_local_git_adapter_contract` |

## 2. Defects found by the suite (fixed)

1. **`hosts` table missing `display_name`** — every `get_host` raised
   `IndexError`. Fixed by adding the column, an idempotent `ALTER TABLE`
   migration for existing databases, including the column in
   `INSERT OR REPLACE`, and a safe read fallback.
2. **`hosts` table missing `last_seen_at`** — status updates raised
   `sqlite3.OperationalError`. Fixed with the same additive migration.
3. **`link_project_host` used the process-global registry** instead of the
   injected `RegistryV2`, so project linking failed for temporary/tenant
   databases. Fixed to use `self._registry`; `get_projects_for_host` had the same
   bug and was corrected.

These were real runtime failures, not test-only issues: any host read on a fresh
database failed before the fix.

## 3. What is still not implemented

The following required contract behaviours have no adapter implementation, so
they cannot pass and must not be claimed:

- connect / open session
- host health check with live latency
- read a file through the host abstraction
- list a host collection through the host abstraction
- disconnect
- reconnection and failure recovery
- host switching and failover
- audit events for host lifecycle changes
- permission-denied handling at the host boundary

Local adapters expose discovery, metadata, capability and workspace-access
helpers, but not a full host session lifecycle.

## 4. Verdict by adapter

| Adapter | Registry/metadata contract | Full operational contract |
| --- | --- | --- |
| Local filesystem | PASSES | NOT IMPLEMENTED (no session/health/read lifecycle) |
| Local Git | PASSES | NOT IMPLEMENTED |
| GitHub shim | Covered indirectly by provider tests | NOT IMPLEMENTED as a host |
| Cloud/remote families | N/A | DESCRIPTOR_ONLY |

No host is declared operational. `ogx host list` remains `No hosts registered.`
on a fresh state directory.

## 5. Reproduce

```bash
cd GUARDIAN-X
python -m pytest -q tests/test_host_contract.py
# 12 passed
```

The suite uses temporary SQLite databases and temporary directories; it performs
no network I/O.
