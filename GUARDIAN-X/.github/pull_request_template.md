## Summary

<!-- What changed and why? -->

## Safety and security checklist

- [ ] Default target scans remain read-only; project-root integration is opt-in, allowlisted, conflict-preserving, and hash-tracked.
- [ ] No target project code, scripts, tests, or plugins are executed.
- [ ] No secrets, tokens, or private repository content are added to fixtures or logs.
- [ ] Any network access is opt-in and documented.
- [ ] Any filesystem output is constrained to OGX-owned external state.
- [ ] New behavior has regression tests, including hostile path/symlink cases where relevant.
- [ ] Documentation and CHANGELOG are updated.

## Validation

- [ ] `python -m compileall -q src tests`
- [ ] `python -m unittest discover -s tests -v`
- [ ] Package build completed
- [ ] Dependency/security checks completed or limitations stated
