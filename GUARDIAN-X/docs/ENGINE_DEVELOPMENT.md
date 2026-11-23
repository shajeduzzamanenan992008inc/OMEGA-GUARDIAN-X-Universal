# Engine Development Guide

## 1. Design goals

The universal layer adds engines without changing the original `ogx` scanner and report contracts.

- Engines are passive: static, local, read-only and bounded.
- Engines declare capability status instead of implying support.
- Engine failures are isolated; one engine cannot abort a project analysis.
- Findings keep the legacy `ogx.models.Finding` shape internally and are converted to `UnifiedFinding` for the universal report.
- No new runtime dependency is required to add a language engine.

## 2. Package layout

```text
core/ogx/
  rules.py                 shared declarative rules
  workspace.py             safe bounded file access
  findings.py              unified finding schema
  pipeline.py              report orchestration
  engines/
    contract.py            BaseEngine, Capability, EngineContext, EngineResult
    language_specs.py      declarative technology specifications
    language.py            generic spec-driven engine
    android.py             dedicated Android engine
    build.py               build/test discovery adapters
    registry.py            registration and selection
```

## 3. Engine contract

Every engine subclasses `BaseEngine` and provides:

| Member | Requirement |
|---|---|
| `engine_id` | Stable, unique, non-`base`; used by CLI `--engines`. |
| `engine_version` | Engine implementation version, not project version. |
| `display_name` | Human-readable label. |
| `category` | Logical group such as `language`, `mobile`, `infrastructure`. |
| `language_keys` | Technology keys owned by the engine. |
| `detect(context)` | Return whether the project matches this engine. |
| `capabilities(context)` | Return all six operations with a status and detail. |
| `analyze(context)` | Return findings/facts/warnings. |
| `dependencies(context)` | Optional declaration inventory. |
| `build(context)` / `test(context)` | Optional discovery only. |
| `diagnose(context)` | Optional static diagnostics. |
| `health()` | Passive health metadata; no execution or network. |

`BaseEngine.evaluate` calls detection, negotiates capabilities, runs only operations whose status is not `unsupported`, catches failures per operation, and returns an `EngineResult`.

### Operation return shape

An operation returns a dictionary. `findings` is removed from the payload and converted to a finding list; `warnings` is collected.

```python
{
    "findings": [Finding(...)],
    "facts": {...},          # bounded, non-secret observations
    "warnings": ["..."],
}
```

Never return raw secret values, full environment dumps, or unbounded file content.

## 4. Adding a language or technology

Most additions should be a declarative `LanguageSpec` in `language_specs.py`:

```python
LanguageSpec(
    "example", "Example", (".ex",),
    file_names=("Examplefile",),
    manifests=("example.lock",),
    lockfiles=("example.lock",),
    build_adapter_ids=("example-build",),
    test_languages=("Example",),
    parser="c-like",
    notes=("No Example compiler is invoked.",),
)
```

Then:

1. Register any build adapter in `build.py`; it may propose commands but must set `execution: not_executed`.
2. Add test-discovery patterns only if conventional layouts are known.
3. If the manifest has a dependency parser, add it to `_parse_manifest` and `_manifest_parser_available`.
4. Add detection/analysis tests in `tests/test_engines.py`.
5. Update `docs/LANGUAGE_SUPPORT_MATRIX.md` and the capability matrix.

Do not add a new parser dependency without an explicit product/security decision. Standard library first.

## 5. Adding a dedicated engine

Create a module under `core/ogx/engines/`, subclass `BaseEngine`, register it in `default_engines()`, and add tests. Android is the reference implementation.

Minimum test checklist:

- unique engine ID and passive health;
- detection true and false fixtures;
- every capability status uses the stable vocabulary;
- malformed input returns a finding or warning, never a crash;
- path traversal, symlink and oversized-file behavior;
- secret-like values are absent from all serialized output;
- build/test operations report `executed: false`;
- failure isolation: `detect`, `capabilities`, `analyze` and `_signals` exceptions;
- CLI selection via `ogx engines inspect` and `ogx analyze --engines`.

## 6. Capability status rules

Use:

- `supported` only when the implementation is tested for that exact scope.
- `partial` when output is heuristic, declaration-only, or lacks resolution.
- `not_configured` when a manifest, tool, SDK or wrapper is absent.
- `unsupported` when no implementation exists.
- `failed` only as an evaluated runtime result, not as a declared capability.

A build tool being present does not authorize execution. The status remains discovery-only and `execution_performed` remains `false`.

## 7. Rules and evidence

Rules live in `rules.py` and must:

- use stable `OGX-...` IDs;
- include title, severity, category, message and recommendation;
- redact credentials at the source;
- avoid claiming exploitability from a pattern match;
- be bounded and side-effect free.

Use `redact_evidence()` for evidence values. Use placeholder detection to avoid reporting documentation examples as secrets. XML parsing must reject DTD/entity declarations. YAML is structural-only unless a parser is deliberately added.

## 8. Security invariants

Every new engine must preserve:

1. No target code execution.
2. No package manager, build wrapper, test runner or device command execution.
3. No network access.
4. No writes inside the project root.
5. No symlink traversal.
6. Bounded file count, size and total bytes.
7. No secret values in findings, facts, reports, logs or test output.
8. Explicit `execution_performed: false` and `network_access: false`.
9. Failure isolation at the engine and operation level.
10. Honest `not_configured`/`unsupported` reporting when evidence is unavailable.

## 9. Running the tests

```powershell
python -m compileall -q core _dev/tests
$env:PYTHONPATH = "src"
python -m unittest discover -s _dev/tests -v
```

Before release, also build the wheel and inspect its contents:

```powershell
python -m pip wheel --no-deps --no-build-isolation . -w dist
```
