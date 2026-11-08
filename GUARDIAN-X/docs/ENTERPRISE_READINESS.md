# Enterprise and Commercial Readiness

## Architecture foundations shipped in 0.4.0a1

- External local engine and project registry.
- Local and Git-hosted repository operation foundations.
- Explicit approval gates for remote writes.
- Organization/member/project/license/usage/audit data schema and local SQLite prototype.
- Declarative skill and plugin/agent manifest schemas.
- JSON/Markdown outputs and best-effort CycloneDX output.
- Bounded resource use and optional disposable sandbox.

## Production blockers

1. PostgreSQL schema migrations, backup/restore and disaster recovery tests.
2. OIDC/SAML integration, MFA, service identities, session security and server-side authorization middleware.
3. Per-tenant encryption boundaries, key management, rotation, data residency and customer-controlled retention/deletion.
4. Multi-node job queues, idempotency, rate limiting, quotas and concurrency controls.
5. API gateway, TLS configuration, observability, alerting and incident response.
6. Signed installers, wheels, container images, SBOM/provenance and reproducible release pipeline.
7. Independent penetration testing, vulnerability disclosure and security response SLAs.
8. EULA, Privacy Policy, DPA, marketplace terms, third-party license review, trademark review and commercial tax/payment compliance.
9. Support tiers, upgrade compatibility, migration policy, deprecation policy and SLA measurement.
10. Formal mapping and audits before any certification claim (SOC 2, ISO 27001, etc.).

## Commercial product packaging proposal

- Community: local core and basic report functionality.
- Professional: developer automation and premium analyzers.
- Team: shared projects, policies and review workflows.
- Business: central governance, audit and CI integrations.
- Enterprise: SSO, advanced RBAC, private adapters, self-hosted controls and support SLA.
- SaaS: managed control plane with strongly isolated tenant services.
- Marketplace: signed, reviewed and versioned skills/plugins.

Do not promise a feature because it appears in the roadmap or dashboard mockup. Edition entitlements must correspond to implemented, tested and documented features.
