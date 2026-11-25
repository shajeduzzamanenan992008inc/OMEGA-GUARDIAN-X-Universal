# Milestone I.4 — Archive Integrity Verification

Date: 2026-11-26
Branch: `main`
HEAD: `757d6b6c519a30487a3bd46db30011ece30b1bdb`
Generator under test: `GUARDIAN-X/_dev/tools/make_dist.py` (working-tree fix, uncommitted)

## 1. Method

The archive must be verifiable from the exact committed tree, not only from the modified working tree. Because the generator fix is uncommitted, verification used this procedure:

1. `git archive --format=zip HEAD` → clean export of `757d6b6` in a temporary directory.
2. Build the archive with the **unmodified** generator from that export (before state).
3. Overlay the fixed `_dev/tools/make_dist.py` into the clean export.
4. Build the archive again from the same clean export (after state).
5. Run the exact forbidden-path and required-file assertions from `.github/workflows/ci.yml`.
6. Extract the after archive into a fresh directory and run `init.py status` and `python -m ogx --version`.
7. Build the archive from the current working tree as a secondary check.

No repository file outside the temporary export was used as archive input.

## 2. Before / after (clean HEAD export)

| Metric | Before | After |
|---|---|---|
| Files | 305 | **304** |
| Bytes | 527,494 | 526,699 |
| SHA-256 | `96fef0ac1e061f5a97e11d4ebf79d445b17a1f27b9b8d44e1c2f828e6742324b` | `cbb9ffc366da407d2bdc4df6071c5b1e7f30588dd20ebabee6124d3781b39203` |
| Forbidden entries | 1 | **0** |

Exact set difference:

```
removed: ['omega-shield/GUARDIAN-X/.github/RELEASE_NOTES.md']
added  : []
```

No other entry changed. The 305-file Milestone I.3 baseline is accounted for: 305 − 1 nested `.github` file = 304.

## 3. Forbidden-path assertion (verbatim CI policy)

```python
forbidden = ("/tests/", "/_dev/", "/.github/", "/release/", "/src/", "__pycache__", "/results.txt")
bad = [name for name in names if any(token in name for token in forbidden)]
```

Result on the after archive: `bad = []` → **PASS**.

The assertion was not modified, relaxed, or bypassed.

## 4. Required-file assertion

```python
required = ("omega-shield/init.py", "omega-shield/core/ogx/__init__.py")
missing = [name for name in required if name not in names]
```

Result: `missing = []` → **PASS**.

Additional presence checks:

| Entry | Present |
|---|---|
| `omega-shield/init.py` | yes |
| `omega-shield/core/ogx/__init__.py` | yes |
| `omega-shield/VERSION` | yes |
| `omega-shield/shield.toml` | yes |
| `omega-shield/core/` | 117 entries (unchanged) |
| `omega-shield/docs/` | 76 entries (unchanged) |
| `guard/`, `devkit/`, `link/`, `examples/`, `schemas/`, `scripts/` | present |

## 5. Hygiene scan of the after archive

| Pattern | Hits |
|---|---|
| `/.github/` | 0 |
| `/tests/` | 0 |
| `/_dev/` | 0 |
| `/src/` | 0 |
| `/release/` | 0 |
| `__pycache__` | 0 |
| `.pytest_cache` | 0 |
| `/build/` | 0 |
| `/dist/` | 0 |
| `.egg-info` | 0 |
| `/.git/` | 0 |
| `.env` (other than `.env.example`) | 0 |
| `.pyc`, `.whl`, `.zip`, `.db`, `.sqlite*` | 0 |

## 6. Determinism

- Entries are emitted in sorted path order; two consecutive builds of the same clean export produced **identical entry lists and identical SHA-256** (`cbb9ffc3…39203`).
- The implementation writes source file mtimes into ZIP headers, so the byte hash is not reproducible across exports or filesystems with different timestamps. Entry-list determinism is guaranteed; byte-level reproducibility is not. This is unchanged from the prior implementation and was not expanded in scope.

## 7. Working-tree archive

| Metric | Before | After |
|---|---|---|
| Files | 324 | **318** |
| SHA-256 | `2808da3b0240a328d11866bee39ce127ef5c2130142ef8cb5081d3b12141d2bb` | `a393515a475adb3c61a70de9b0e7b67cf3fb7352c30116a4f42d84f40f4cf4f7` |

The six removed entries are:

1. `omega-shield/GUARDIAN-X/.github/RELEASE_NOTES.md` (nested repository metadata);
2. five files under `omega-shield/core/omega_guardian_x.egg-info/` (`PKG-INFO`, `SOURCES.txt`, `dependency_links.txt`, `entry_points.txt`, `top_level.txt` — ignored in-tree build metadata).

The remaining 318 − 304 = 14 difference from the clean export is exactly the user's untracked relocated Milestone I/I.1/I.2 reports under `GUARDIAN-X/docs/`. No other working-tree-only file ships.

## 8. Installation and CLI smoke (after archive)

Extracted `omega-shield/` into a fresh temporary host directory:

```
init.py status exit: 0
core       : ogx 0.8.0a1 (python 3.15.2)
state home : <temp>/state  (outside host)
ogx --version exit: 0 | ogx 0.8.0a1
```

`init.py status` reported `gates: pre-commit=no git repo, pre-push=no git repo` because the extraction host is not a Git repository; this is expected and not a packaging failure.

## 9. Conclusion

The generated drop-in archive from a clean export of `757d6b6` no longer contains the nested `.github` directory. The CI forbidden-path assertion passes unmodified, all required entries remain, and the only removed file is the unintended nested release-note mirror. The archive is independently installable and its CLI entry point works.
