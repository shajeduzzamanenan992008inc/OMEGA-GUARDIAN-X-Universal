# Workflow location notice

This directory holds the **standalone-distribution** CI for the `GUARDIAN-X/`
product tree. It is only used when `GUARDIAN-X/` is exported as its own
repository root (for example the `omega-shield` drop-in export built by
`_dev/tools/make_dist.py`).

In the `OMEGA-GUARDIAN-X-Universal` monorepo the **authoritative** workflows
live one level up at `/.github/workflows/` (`OGX CI`, `OGX Code Security`,
`GitHub Configuration Validation`, `Dependency Review`). GitHub Actions only
reads `.github/workflows/` at the repository root, so the files in *this*
directory never run there. They are kept as a portable, self-contained copy so
the extracted product is not left without CI, and they are validated for YAML
syntax by `GitHub Configuration Validation`.

Rules for keeping the two sets honest:

1. Any behavioural change to a check belongs in the repository-root workflow
   first; mirror it here only if it also makes sense for the standalone tree.
2. Paths here are relative to `GUARDIAN-X/` (no `GUARDIAN-X/` prefix and no
   `working-directory:`); the root workflows prefix every path with
   `GUARDIAN-X/`.
3. Neither set may use `|| true`, `continue-on-error` or skipped steps to hide
   a failure. A suite that collects zero tests must fail the job.
