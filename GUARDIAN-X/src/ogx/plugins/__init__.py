"""Plugin trust, permission, lifecycle and isolation runtime (PLG-02..PLG-05).

Third-party *code execution* remains disabled by design (PLG-06): this package
implements the controls that must exist before any owner-approved enablement,
and every execution path fails closed unless trust, permissions and an
isolation backend are all satisfied.
"""

from .manifest import (
    KNOWN_CAPABILITIES,
    ManifestError,
    PluginManifest,
    parse_manifest,
    parse_version,
    satisfies,
    verify_files,
)
from .permissions import (
    CAPABILITY_RISK,
    SENSITIVE_CAPABILITIES,
    Grant,
    PermissionDenied,
    PermissionPolicy,
    make_grant,
    revoke_grant,
)
from .runner import (
    DockerIsolationBackend,
    ExecutionResult,
    PluginRunner,
    ProcessIsolationBackend,
    ResourceLimits,
    RunnerError,
)
from .store import LifecycleError, PluginStore, STATES
from .trust import (
    SignatureEnvelope,
    TrustError,
    TrustPolicy,
    TrustStore,
    TrustedKey,
    fingerprint,
)

__all__ = [
    "CAPABILITY_RISK",
    "DockerIsolationBackend",
    "ExecutionResult",
    "Grant",
    "KNOWN_CAPABILITIES",
    "LifecycleError",
    "ManifestError",
    "PermissionDenied",
    "PermissionPolicy",
    "PluginManifest",
    "PluginRunner",
    "PluginStore",
    "ProcessIsolationBackend",
    "ResourceLimits",
    "RunnerError",
    "SENSITIVE_CAPABILITIES",
    "STATES",
    "SignatureEnvelope",
    "TrustError",
    "TrustPolicy",
    "TrustStore",
    "TrustedKey",
    "fingerprint",
    "make_grant",
    "parse_manifest",
    "parse_version",
    "revoke_grant",
    "satisfies",
    "verify_files",
]
