---
applyTo: "src/**/*.py,tests/**/*.py"
---
# Python-specific instructions

- Support Python 3.11+ and prefer the standard library in the runtime.
- Use type annotations and focused pure functions where practical.
- Keep filesystem operations bounded and rooted in the intended directory.
- Target repository paths are read-only. All writes must be under OGX-owned state.
- Do not follow symlinks when traversing target repositories; reject symlinked OGX state and output paths.
- Do not invoke subprocesses or execute project code in scan paths.
- Redact credential values; never include raw secrets in evidence, exceptions, or logs.
- Add unittest coverage for edge cases, malicious paths, symlinks, partial scans, and no-write guarantees.
