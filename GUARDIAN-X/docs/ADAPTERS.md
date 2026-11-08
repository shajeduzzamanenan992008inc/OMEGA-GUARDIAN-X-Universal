# Adapter and Extension Contracts

## Git provider adapters

Responsibilities: provider identification, repository metadata, auth from environment/credential helper, branch/PR/MR operations and normalized errors. Tokens must never be persisted or included in logs. All mutating methods require an explicit approval object and audit event. Generic Git supports repository operations but does not assume an issue/PR API.

## AI agent adapter

Manifest schema: `docs/schemas/ogx-agent-adapter.schema.json`.

Required contract: identify, discover capabilities, validate configured credentials/interface, submit task, normalize result, cancel, apply output limits, attach audit ID and declare required permissions. A provider not detected is reported as adapter-defined, not as an available runtime.

## Skill manifest

Schema: `docs/schemas/ogx-skill.schema.json`. Skills in this release are declarative Markdown and JSON. Manifest validation is not a security certification. Skill execution remains disabled.

## Plugin manifest

Schema: `docs/schemas/ogx-plugin.schema.json`. Future plugin runtime must enforce signed publisher identity, API version compatibility, isolated process/container execution, capability grants, time/resource limits, network policy, audit logs and revocation. No third-party plugin code is executed in 0.4.0a1.

## Capability negotiation

Adapters declare operations as `available`, `partial`, `not-configured` or `unsupported`. OGX may not claim an operation works merely because a connector name appears in a registry.
