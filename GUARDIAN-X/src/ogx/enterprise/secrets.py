"""Tenant secrets encrypted at rest through the KMS boundary (ENT-06).

Values are envelope-encrypted per *(tenant, name)* pair: the associated data is
``"<organization_id>:<name>"``, so a ciphertext moved to another tenant or
renamed is rejected by the AEAD instead of silently decrypting.

Only owners/admins may write or rotate; reads require ``secret.read`` and are
audited **without** the value. Rotation re-encrypts every stored secret under a
new key version, which is the only operation that touches plaintext in a loop.
"""

from __future__ import annotations

import json
from typing import Any

from .authz import Action, PolicyEngine, Principal, Resource
from .crypto import EnvelopeCipher, KMSProvider, LocalKeyring
from .store import EnterpriseStore

PURPOSE = "tenant-secrets"


class SecretsVaultError(RuntimeError):
    """Base error for the tenant secrets vault."""


class SecretsVault:
    def __init__(self, store: EnterpriseStore, provider: KMSProvider | None = None,
                 policy: PolicyEngine | None = None) -> None:
        self.store = store
        self.provider = provider or LocalKeyring()
        self.cipher = EnvelopeCipher(self.provider, purpose=PURPOSE)
        self.policy = policy or store.policy

    @staticmethod
    def _validate_name(name: str) -> str:
        value = str(name or "").strip()
        if not value or len(value) > 64:
            raise ValueError("Secret name is required (max 64 characters).")
        if not all(character.isalnum() or character in "-_." for character in value):
            raise ValueError("Secret names may only contain letters, digits, '-', '_' and '.'.")
        return value

    @staticmethod
    def _aad(organization_id: str, name: str) -> bytes:
        return f"{organization_id}:{name}".encode("utf-8")

    def _require(self, organization_id: str, actor_subject: str, action: Action,
                 resource_kind: str = "secret", resource_id: str | None = None) -> PolicyEngine:
        role = self.store.member_role(organization_id, actor_subject)
        if role is None:
            raise PermissionError("Not a member of this organization.")
        self.policy.require(
            Principal(subject=actor_subject, roles=(role,), tenant_id=organization_id),
            action,
            Resource(kind=resource_kind, id=resource_id, tenant_id=organization_id),
        )
        return self.policy

    def set(self, organization_id: str, name: str, value: str, actor_subject: str) -> dict:
        name = self._validate_name(name)
        if not isinstance(value, str) or not value:
            raise ValueError("Secret value must be a non-empty string.")
        if len(value.encode("utf-8")) > 64 * 1024:
            raise ValueError("Secret value exceeds the 64 KiB limit.")
        self._require(organization_id, actor_subject, Action.SECRET_WRITE, resource_id=name)
        sealed = self.cipher.seal(value, aad=self._aad(organization_id, name))
        envelope = json.loads(sealed)
        self.store.upsert_tenant_secret(organization_id, name, sealed, envelope["key_id"])
        self.store.record_audit(
            organization_id, "secret.write", "tenant_secret", name,
            {"key_id": envelope["key_id"], "bytes": len(value.encode("utf-8"))},
            actor_label=actor_subject,
        )
        return {"organization_id": organization_id, "name": name,
                "key_id": envelope["key_id"], "stored": True}

    def get(self, organization_id: str, name: str, actor_subject: str) -> str:
        name = self._validate_name(name)
        self._require(organization_id, actor_subject, Action.SECRET_READ, resource_id=name)
        record = self.store.get_tenant_secret(organization_id, name)
        if record is None:
            raise KeyError("Secret not found.")
        value = self.cipher.open(record["envelope_json"], aad=self._aad(organization_id, name))
        self.store.record_audit(
            organization_id, "secret.read", "tenant_secret", name,
            {"key_id": record["key_id"]}, actor_label=actor_subject,
        )
        return value

    def names(self, organization_id: str, actor_subject: str) -> list[dict[str, Any]]:
        self._require(organization_id, actor_subject, Action.SECRET_READ)
        return self.store.list_tenant_secret_names(organization_id)

    def rotate(self, organization_id: str, actor_subject: str) -> dict[str, Any]:
        self._require(organization_id, actor_subject, Action.KMS_ROTATE)
        previous = self.provider.describe().get("purposes", {}).get(PURPOSE)
        new_key_id = self.provider.rotate(PURPOSE)
        rotated, failed = 0, 0
        for entry in self.store.list_tenant_secret_names(organization_id):
            record = self.store.get_tenant_secret(organization_id, entry["name"])
            if record is None:
                continue
            try:
                plaintext = self.cipher.open(
                    record["envelope_json"], aad=self._aad(organization_id, entry["name"])
                )
            except Exception:
                failed += 1
                continue
            sealed = self.cipher.seal(plaintext, aad=self._aad(organization_id, entry["name"]))
            self.store.upsert_tenant_secret(
                organization_id, entry["name"], sealed, json.loads(sealed)["key_id"]
            )
            rotated += 1
        self.store.record_audit(
            organization_id, "kms.rotate", "tenant_secrets", None,
            {"previous_key_id": previous, "new_key_id": new_key_id,
             "rotated": rotated, "failed": failed},
            actor_label=actor_subject,
        )
        return {"organization_id": organization_id, "previous_key_id": previous,
                "new_key_id": new_key_id, "rotated": rotated, "failed": failed}

    def status(self, organization_id: str, actor_subject: str) -> dict[str, Any]:
        self._require(organization_id, actor_subject, Action.SECRET_READ)
        description = self.provider.describe()
        description["secrets"] = self.store.list_tenant_secret_names(organization_id)
        return description


__all__ = ["PURPOSE", "SecretsVault", "SecretsVaultError"]
