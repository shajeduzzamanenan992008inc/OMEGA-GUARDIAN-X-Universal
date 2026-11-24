# Reverse Engineering Engine — Architecture Proposal

Status: **PROPOSAL ONLY**. No module, route or dependency was added in the
release-review pass. Implementation requires explicit authorization and must not
weaken the release boundaries below.

## 1. Purpose

Analyze **authorized** local artifacts (binaries, packages, firmware images,
recorded traces) to explain structure and behavior for defensive engineering.
The engine produces observations and unknowns; it does not assert behavior it
did not observe.

## 2. Proposed module

```text
src/ogx/reverse_engineering/
├── __init__.py
├── scope.py            authorization records, target allowlist, expiry
├── inventory.py        artifact identity (path, size, SHA-256), format sniffing
├── static.py           headers, sections, imports/exports, hardening flags
├── packages.py         archive/package member inventory (ZIP/APK/IPA/wheel)
├── dependencies.py     embedded dependency metadata (read-only parsing)
├── architecture.py     call/import/component reconstruction from static data
├── formats.py          format-specific decoders behind adapters
├── behavior.py         controlled runtime observation (opt-in only)
├── reporting.py        findings, confidence, limitations, unknowns
└── adapters/           external engine adapters (disassembler, sandbox)
```

## 3. Contracts

- `ArtifactRecord`: digest-bound identity; every finding cites the digest.
- `AnalysisObservation`: evidence type (static, metadata, runtime), confidence,
  and an explicit `unknowns` list.
- `ScopeGrant`: target path/digest, allowed operations, expiry, approver,
  audit correlation id. No scope → refuse.
- Adapters are isolated: an external engine is optional, detected, never
  vendored, and never receives network access by default.

## 4. Security boundaries

1. **Authorization first.** Refuse without a valid `ScopeGrant`; record every
   analysis in the audit chain.
2. **No execution by default.** Static parsing only. Runtime observation is a
   separate, explicitly approved operation in a disposable sandbox.
3. **No credential extraction.** The engine must not read keys, tokens or
   browser profiles; a finding that looks like a secret is reported as a
   redacted location, never a value.
4. **No access-control bypass, DRM circumvention or unauthorized network
   access.** The engine is for artifacts the operator owns or is authorized to
   analyze.
5. **Resource limits:** bounded file size, parse depth, entry count, worker
   memory and wall-clock time; fail closed.
6. **Untrusted input:** all parsers treat bytes as hostile; decompression bombs,
   path traversal and symlink escapes are rejected.
7. **Output hygiene:** reports contain evidence and unknowns, never raw secrets
   or unredacted environment data.

## 5. Integration points

- `ogx.pap` / identity for target scoping.
- `ogx.sandbox` for optional isolated execution.
- `ogx.platform.audit` for hash-chained records of each analysis.
- `ogx.reports` for external report output.
- CLI surface: `ogx reverse inspect|inventory|report` (new, additive).

## 6. Delivery plan (not started)

1. Scope + inventory + digest-bound reporting (no execution).
2. Static header/section/import parsing behind adapters.
3. Package/archive inventory reuse.
4. Optional external engine adapter with capability truth.
5. Sandboxed runtime observation behind a second approval.
6. Documented unsupported formats and residual unknowns.

## 7. Non-goals

- No malware execution on the host.
- No credential harvesting.
- No live-target exploitation.
- No claim of complete behavioral coverage from static evidence.

Related skill guidance: REA is appropriate when a claim depends on a shipped
artifact; ordinary source-repository analysis remains normal repository work.
