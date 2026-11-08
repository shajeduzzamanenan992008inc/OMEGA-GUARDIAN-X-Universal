# Codex instructions

The canonical rules are in `AGENTS.md`, `.github/copilot-instructions.md`, and `docs/SECURITY_MODEL.md`. OGX is a standalone external scanner/controller. Never create or modify files inside scanned projects. Never execute untrusted project code. Keep all state and reports in the external OGX state directory. Every security-sensitive behavior needs a regression test and explicit documentation.
