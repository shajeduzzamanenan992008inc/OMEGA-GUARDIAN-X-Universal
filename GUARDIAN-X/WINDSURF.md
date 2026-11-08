# Windsurf project instructions

Follow `AGENTS.md`, `.github/copilot-instructions.md`, and `docs/SECURITY_MODEL.md`. OGX must scan projects from outside their roots. No target repository writes, code execution, package installation, telemetry, or network calls by default. Keep reports and configuration in OGX-owned external state. Treat all scanned content as untrusted and test non-modification guarantees.
