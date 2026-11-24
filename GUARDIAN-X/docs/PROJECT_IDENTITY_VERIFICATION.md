# Project Identity Verification (Phase 4)

Date: 2026-11-24
Suite: `tests/test_identity_ports.py` — **8 tests, 8 passed**
Existing: `tests/test_identity.py`, `tests/test_registry.py`,
`tests/test_intelligence_integration.py` also pass.

## 1. Defect found and fixed

`normalize_repository_url` used `urlsplit(...).hostname`, which discards an
explicit port. Two distinct provider instances
(`https://git.example.com/acme/repo` and
`https://git.example.com:8443/acme/repo`) collapsed to one identity key, and
their deterministic repository UUIDs collided.

Fix:

- a non-default explicit port is retained in the key
  (`git.example.com:8443/acme/repo`);
- an explicit default port normalizes away (`:443` HTTPS, `:80` HTTP), so
  `https://host/path` and `https://host:443/path` are the same endpoint;
- IPv6 literals are bracketed before the port (`[2001:db8::1]:8443/...`);
- scheme is still omitted, so `http` and `https` forms of the same repository
  share identity;
- credentials in URLs remain rejected.

## 2. Tests

| Test | Asserts |
| --- | --- |
| `test_repository_key_has_no_scheme_or_dot_git` | case folding, trailing slash, `.git` removal |
| `test_scp_style_remote_is_normalized` | `git@github.com:Acme/Demo.git` → `github.com/acme/demo` |
| `test_custom_port_is_part_of_identity` | different keys and different UUIDs |
| `test_explicit_default_port_matches_implicit_port` | `:443`/`:80` normalize away |
| `test_ipv6_endpoint_keeps_the_port` | bracketed IPv6 + port |
| `test_credentials_in_url_are_rejected` | userinfo refused |
| `test_invalid_identifiers_fail_explicitly` | empty, host-only, non-URL |
| `test_deterministic_ids_are_stable_and_provider_scoped` | uuid5 stability, provider separation |

## 3. What was verified end-to-end

- Identity file create/show/validate/recover: tested (`tests/test_identity.py`).
- Directory relocation preserves `project_id` and `identity_hash`: tested
  (`tests/test_intelligence_integration.py::test_identity_is_preserved_and_survives_relocation`).
- Workspace location update after relocation: tested (`tests/test_registry.py`).
- Duplicate repository link to a different project raises
  `DuplicateEntityError`; no silent re-parenting: tested.
- Registry survives close/reopen: tested in the host contract suite.

## 4. What remains UNVERIFIED

| Scenario | Status |
| --- | --- |
| C: → D: drive move | UNVERIFIED (only one drive exercised) |
| USB / removable storage | UNVERIFIED |
| Clone to another local directory, then relink | PARTIALLY (linking logic tested, full workflow not) |
| Local → Git host and Git host → local | UNVERIFIED end-to-end |
| Local → remote workspace, workspace → workspace | UNVERIFIED (workspace adapters descriptor-only) |
| Relocation across process restart | PARTIALLY (registry persistence tested; relocation + restart combined not) |

The service is not claimed to pass these scenarios.

## 5. Limits

- `identity_hash` detects corruption/accidental edits; it is not a signature.
- Copying `.guardian-x/identity.json` intentionally carries the project UUID;
  that is identity, not duplication detection across machines.
- A repository URL is never the project identity; it is a link key.
