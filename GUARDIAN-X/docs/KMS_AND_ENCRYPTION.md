# Encryption at rest and key management (ENT-06)

**Status:** envelope encryption, key rotation, revocation and tenant-secret
storage are implemented and tested with the **local test key provider**. No
managed KMS is integrated, so production key custody remains a gap.

**Review date:** 2026-11-28

## 1. Why this exists

Redaction is not encryption. Values OGX persists for an organization (a mail
provider key, a webhook signing secret, a future credential blob) must be
unreadable in the database file and bound to their tenant so a copied ciphertext
cannot be replayed under another name or tenant.

## 2. Primitives

`src/ogx/enterprise/crypto.py` implements, in portable Python:

* **ChaCha20** (RFC 8439 §2.3–2.4) with a 32-byte key, 96-bit nonce and 32-bit
  block counter;
* **Poly1305** (RFC 8439 §2.5) with the standard `r` clamping;
* **ChaCha20-Poly1305 AEAD** (RFC 8439 §2.8): `ciphertext || tag`, one-time key
  from block 0, `pad16(aad) || pad16(ct) || len(aad) || len(ct)` MAC input.

`tests/test_crypto_kms.py` checks the zero-key ChaCha20 block vector
(RFC 8439 §2.3.2), the Poly1305 "Cryptographic Forum Research Group" vector
(§2.5.2), round-trip, wrong-key, tampered-ciphertext, wrong-AAD and truncated
ciphertext behaviour. A wrong implementation cannot pass those vectors.

## 3. Envelope format

```json
{
  "v": 1,
  "alg": "chacha20poly1305",
  "key_id": "k_…",
  "purpose": "tenant-secrets",
  "nonce": "base64",
  "ciphertext": "base64(ciphertext||tag)",
  "aad_sha256": "hex"
}
```

Associated data for a tenant secret is `"<organization_id>:<name>"`. Moving the
envelope to another tenant or renaming the secret changes the AAD and fails
authentication (`AuthenticationFailed`) instead of decrypting silently. The
`purpose` field is also checked, so an envelope cannot be replayed across
purposes.

## 4. Key provider

`KMSProvider` is the seam for a managed service. The shipped provider is
`LocalKeyring`:

* key material generated with `secrets.token_bytes(32)`, stored in
  `OGX_HOME/enterprise/kms-keys.json` created with mode `0600` and written with
  fsync + `os.replace`;
* a `purposes` map points at the *current* key id per purpose;
* `rotate(purpose)` creates a new key and marks the previous one `retired`
  (still decryptable); `revoke(key_id)` makes decryption fail with
  `KeyRevoked`;
* `describe()` returns key ids, purposes, statuses and timestamps only — never
  key material. The API/CLI never exposes the file contents.

Honest limits (also in the module docstring): pure-Python AEAD is not
constant-time; the key file is protected only by filesystem permissions; there
is no hardware root of trust, no external audit and no per-caller key policy.
This provider exists so the format, rotation, revocation and restore paths are
real and testable, **not** to satisfy a production KMS requirement.

## 5. Tenant secrets

`src/ogx/enterprise/secrets.py` (`SecretsVault`) stores values through
`tenant_secrets(organization_id, name, envelope_json, key_id, …)`:

```bash
printf '%s' "$MAIL_KEY" | ogx enterprise secret-set --org <id> \
  --name mail-api-key --actor-subject owner@example.com --stdin
ogx enterprise secret-get  --org <id> --name mail-api-key --actor-subject owner@example.com
ogx enterprise secrets     --org <id> --actor-subject owner@example.com
ogx enterprise kms-status  --org <id> --actor-subject owner@example.com
ogx enterprise kms-rotate  --org <id> --actor-subject owner@example.com
```

* `secret.write` and `kms.rotate` require `owner`/`admin`; `secret.read`
  requires the same; auditors and developers are refused.
* `--stdin` is mandatory for writes so the value never appears in shell history
  or in the process argument list.
* Writes/rotations and reads are audited with the key id and byte count —
  never the value.
* Rotation re-encrypts every secret of the tenant under the new key and reports
  `rotated`/`failed` counts. A failed re-encryption is counted and left
  untouched rather than silently dropped.
* Cross-tenant access is refused by the policy engine (`deny.tenant_mismatch`)
  before decryption is attempted.

## 6. Evidence

```bash
python -m pytest tests/test_crypto_kms.py -q     # 14 passed
python -m pytest -q                              # 1182 passed, 23 skipped
```

Covered: RFC vectors, AEAD tamper/AAD/wrong-key rejection, key rotation keeping
old ciphertext readable, revocation, unknown key, purpose binding, envelope JSON
validation, tenant/name binding, cross-role denial, rotation re-encryption and
audit records.

## 7. Remaining gaps

* A managed KMS provider (AWS KMS / GCP KMS / Azure Key Vault / Vault Transit)
  is not integrated; only the interface exists.
* No key-availability test (KMS outage), no escrow/recovery drill, no
  multi-region key replication.
* Encryption in transit is delegated to the operator's TLS reverse proxy; the
  API itself is loopback HTTP.
