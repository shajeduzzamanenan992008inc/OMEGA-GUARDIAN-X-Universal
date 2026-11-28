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

* The manifest is **not signed**. An attacker who can rewrite artifact *and*
  manifest consistently is not detected; signing needs an owner-controlled key
  and is not implemented.
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
