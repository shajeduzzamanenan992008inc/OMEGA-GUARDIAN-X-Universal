# OMEGA GUARDIAN X — License Adoption Package

## Files

- `LICENSE` — canonical repository-root license. Place at the root of `OMEGA-GUARDIAN-X-Universal`.
- `GUARDIAN-X/LICENSE` — identical nested copy required by `GUARDIAN-X/pyproject.toml` so package builds can include the license file.
- `GUARDIAN-X/pyproject.toml` — metadata-aligned copy of the currently inspected `GUARDIAN-X/pyproject.toml`; changes the custom license identifier from `LicenseRef-OMEGA-GUARDIAN-X-Proprietary-Alpha` to `LicenseRef-OMEGA-GUARDIAN-X-PANC-1.1` and continues to include `LICENSE` in package metadata.

## Adoption steps (not performed)

1. Copy the package-root `LICENSE` to the repository root.
2. Copy `GUARDIAN-X/LICENSE` into the repository's `GUARDIAN-X/` directory.
3. Apply the provided `GUARDIAN-X/pyproject.toml` or make the same single identifier change in the current file.
4. Update README wording that currently says the license file is absent and add a link to the root `LICENSE`.
5. Review ownership and provenance of all first-party files, historical contributions, dependencies, fonts, images, and bundled assets.
6. Run the canonical source tests, drop-in tests, package build, and inspect the generated wheel to confirm it includes the license.
7. Commit the root license and supporting consistency changes. The license becomes effective only when the root `LICENSE` is published on the default branch while the repository is publicly accessible.

## Scope note

The license text is drafted to cover licensable first-party materials throughout this single repository, including the root `.github/` kit and the entire `GUARDIAN-X/` subtree. It excludes other repositories, scanned target-project content, User Data, Security Materials, and third-party dependencies/assets.

No GitHub file has been created, changed, committed, pushed, or merged as part of preparing this package. The pyproject file is a proposed consistency copy, not a repository modification.

## Review note

This is a custom source-available non-commercial license, not an OSI-approved Open Source license. Automated consistency checks do not establish copyright ownership, contributor consent, third-party provenance, or legal enforceability. Obtain qualified legal review before public release or commercial licensing.
