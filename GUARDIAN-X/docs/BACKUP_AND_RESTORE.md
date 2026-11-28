# Backup, Restore and Disaster Recovery (ENT-10)

Status: **IMPLEMENTED and TESTED for the single-node SQLite store.** PostgreSQL
backup/point-in-time recovery, off-site replication and a rehearsed disaster
recovery drill remain **BLOCKED** on infrastructure (see §6).

## 1. Why a file copy is not a backup

The enterprise store runs SQLite in WAL mode, so the database file on disk can
be mid-transaction together with its `-wal`/`-shm` sidecars. Copying the file
therefore produces a snapshot that may not open at all. `ogx` uses SQLite's
online backup API, which produces a transactionally consistent copy while the
database is being used, and then proves the copy is readable.

## 2. What a backup contains

`enterprise/backup.py` publishes two files per backup:

| File | Content |
| --- | --- |
| `enterprise-<UTC>-<id>.sqlite3[.ogxbak]` | the snapshot (plain or sealed) |
| same name + `.json` | the manifest |

The manifest records: format/version, backup id, optional label, creation time,
product version, artifact name, `encrypted`, `key_id`, artifact `sha256` and
`bytes`, `plaintext_sha256` and `plaintext_bytes`, plus the result of
`PRAGMA integrity_check`, the table count and page count of the verified copy.

## 3. Creating a backup

```bash
ogx enterprise backup                       # to OGX_HOME/enterprise/backups
ogx enterprise backup --destination D:/ogx-backups --label nightly
ogx enterprise backup --encrypt             # seal with the local KMS keyring
ogx enterprise backups                      # list manifests (no plaintext read)
```

Steps performed by `create_backup`:

1. refuse a symlinked or missing database;
2. take an online snapshot into a temporary file in the destination directory;
3. open the snapshot read-only and require `PRAGMA integrity_check` = `ok`;
4. optionally seal it in 64 KiB chunks through the `KMSProvider` boundary;
5. compute the artifact digest and size, write the manifest, `fsync` the data;
6. on any failure, delete the partial artifact and manifest.

### Encryption

`--encrypt` uses the same AEAD as tenant secrets (RFC 8439 ChaCha20-Poly1305)
through `LocalKeyring` (a 0600 key file under `OGX_HOME`). The file starts with
a JSON header `{format, version, chunks, plaintext_bytes, backup_id}`; each
following line is one sealed chunk. The associated data binds
`"<backup_id>:<index>:<chunks>"`, so a reordered, truncated, duplicated or
spliced chunk fails authentication instead of decrypting to the wrong bytes.

`LocalKeyring` is a **development/test** provider, not a managed KMS: there is no
hardware root of trust and no remote audit. `--encrypt` protects a backup file
that leaves the database directory; it does not protect against an attacker who
already runs as the same OS user.

## 4. Verifying a backup

```bash
ogx enterprise backup-verify --backup-path <artifact>
```

`verify_backup` recomputes the artifact SHA-256 and size against the manifest,
then either copies (plain) or decrypts (sealed) the artifact into a temporary
file, checks the plaintext digest, opens it read-only and requires
`PRAGMA integrity_check` = `ok`. Only then does it report `{"valid": true, ...}`.
A backup that has never been verified should be treated as unproven.

## 5. Restoring

```bash
ogx enterprise restore --backup-path <artifact> --target D:/ogx/enterprise.sqlite3
ogx enterprise restore ... --target ... --overwrite   # replaces, keeps .pre-restore
```

`restore_backup` verifies first, writes into a temporary file *in the target
directory*, re-checks integrity, moves any existing database to
`<target>.pre-restore`, removes stale `-wal`/`-shm` sidecars, and only then
`os.replace`s the restored file into place. A failed restore therefore never
leaves a half-written database at the target path.

Retention:

```bash
ogx enterprise backup-prune --destination D:/ogx-backups --keep 7
```

Pruning deletes the oldest artifacts together with their manifests and refuses
`keep < 1`, so a typo cannot erase every backup.

## 6. Honest limits and remaining external work

* Manifest authenticity is **symmetric**: `HmacSigningProvider` (HMAC-SHA256,
  0600 key file) detects a manifest rewritten by anyone who does not hold the key,
  but an attacker who can read the key file can forge a manifest and there is no
  third-party verifiability. Managed/HSM asymmetric signing through the
  `SigningProvider` boundary is not implemented, and no production signing key
  exists in this repository.
* This is a single-node SQLite mechanism. There is no PostgreSQL physical/logical
  backup, no WAL archiving, no point-in-time recovery and no cross-region copy.
* RPO/RTO are therefore **not** quantified for production: they depend on how
  often an operator runs `backup` and where the artifact is stored.
* No disaster-recovery drill has been performed against real infrastructure, and
  no off-site/immutable storage is configured.
* Key recovery: losing the `OGX_HOME` keyring makes encrypted backups
  unrecoverable by design; exporting/escrowing that key is an owner decision.

## 7. Test evidence

`tests/test_backup.py` (real files, real restore):

* plain backup → verify → restore → query restored data through `EnterpriseStore`;
* encrypted backup → verify → restore, with ciphertext never containing the
  SQLite header;
* naive byte corruption is rejected by the digest check;
* a valid-JSON chunk with a mutated ciphertext (manifest re-hashed, i.e. a
  manifest-writing attacker) is rejected by AEAD authentication;
* digest/manifest mismatch is rejected;
* restore refuses to overwrite without `--overwrite`, and keeps `.pre-restore`
  when it does;
* backup of a missing database is refused;
* prune keeps the newest N and refuses `keep=0`;
* symlink targets are rejected (skipped on platforms without symlink support).

## 8. Manifest authenticity (signing)

Hashes answer "is this the artifact the manifest describes"; they do not answer
"who wrote the manifest". `enterprise/backup_signing.py` adds that third layer
behind an explicit provider boundary:

```python
class SigningProvider(Protocol):
    algorithm: str
    def active_key_id(self) -> str: ...
    def sign(self, payload: bytes) -> dict: ...
    def verify(self, payload: bytes, signature: dict) -> bool: ...
    def rotate(self) -> str: ...
    def revoke(self, key_id: str) -> None: ...
    def describe(self) -> dict: ...
```

The concrete provider, `HmacSigningProvider`, keeps 32-byte keys in
`OGX_HOME/enterprise/backup-signing-keys.json` (0600, atomic replace) and signs
the canonical manifest bytes (`json.dumps` sorted/compacted, signature field
excluded). Verification uses `hmac.compare_digest`, so a modified manifest,
a replaced `key_id`, an unknown algorithm or a revoked key fails closed.

```bash
ogx enterprise backup --sign                    # sign the new manifest
ogx enterprise backup-verify --backup-path <f> --check-signature
ogx enterprise backup-verify --backup-path <f> --check-signature --require-signature
ogx enterprise restore --backup-path <f> --target <db> --check-signature
ogx enterprise signing-keys                     # key ids/status, never secrets
ogx enterprise signing-key-rotate               # new active key, old key retired
```

Rotation semantics: the previous key becomes `retired` and still verifies old
manifests, so rotating does not orphan existing backups; `revoke` immediately
stops a key from verifying (compromise response). Because the provider is
symmetric it is an **operator-local** authenticity check, not a release-signing
service: a managed KMS/HSM asymmetric implementation is a separate owner
decision and is not claimed here.

Three properties are reported separately by `verify_backup`:
`sha256` (hash integrity), the AEAD result for sealed artifacts, and
`signature.status` — `verified`, `unsigned` or `unverified` (a signature exists
but no provider was configured to check it).

## 9. Manifest schema, path safety, crash recovery and retention

**Strict schema (v2).** Every manifest is validated before it is used: exact
top-level field set (unknown fields are rejected), 32-hex backup id, SHA-256
digests, integer sizes within the 2 GiB cap, boolean `encrypted`, bounded
timestamps/labels/key ids, and an artifact name that must be a bare file name
matching `enterprise-*.sqlite3[.ogxbak]`. Version 1 manifests still verify
(hash and AEAD only); v1 manifests cannot carry a signature.

**Path safety.** Restore refuses a relative target, any target containing `.`
or `..` segments, a target or parent directory that is a symlink, a target that
exists but is not a regular file, an artifact that is a symlink, an artifact
larger than the cap, and a manifest that describes a different file name.

**Crash-safe restore.** Before touching the target, restore writes
`<target>.restore-journal` (version, artifact digest, backup id, stage). It then
builds the new database in a temporary file in the target directory, verifies
it, moves the old database to `<target>.pre-restore`, removes stale
`-wal`/`-shm` sidecars and `os.replace`s the new file into place; only then is
the journal deleted. After an interruption,
`ogx enterprise restore-recover --target <db>` rolls the previous database back
when the target is missing and always removes stray temporary files.

**Retention safety.** `prune_backups` deletes artifacts together with their
manifests, refuses `keep < 1`, and refuses to delete anything when none of the
backups it would keep passes verification — so a retention run cannot leave the
operator with zero valid backups.

**Tests.** `tests/test_backup_security.py` covers a signed round trip, a
manifest whose content is changed after signing, a replaced artifact, a missing
key, a wrong key, a revoked key, key rotation keeping old manifests valid, a
forged signature algorithm, schema rejection of `../`, absolute and nested
artifact names, oversize/negative/boolean sizes, unknown manifest fields,
relative/`..`/directory restore targets, interrupted-restore rollback, journal
cleanup on success, prune refusal when the newest backup is corrupt, and that
`describe()` never prints key material.
