# PostgreSQL backend, migrations and distributed queue (ENT-02, ENT-07)

**Status:** implemented and verified against a real PostgreSQL 18.7 server on
2026-11-28. Production hosting, HA, PITR and an approved driver decision remain
owner-controlled (see `OWNER_DECISIONS_AND_EXTERNAL_PREREQUISITES.md`).

## 1. Enabling the backend

SQLite remains the default. Set one environment variable to switch the
enterprise store and the durable queue to PostgreSQL:

```text
OGX_DATABASE_URL=postgresql://ogx_app@db.example.internal:5432/ogx
```

Install one optional driver (the base package stays stdlib-only):

```text
pip install "omega-guardian-x[postgres]"     # psycopg 3, recommended
# or an existing psycopg2 installation is accepted
```

The URL is never logged. `ogx enterprise backend` prints only
`scheme://host:port/database` (credentials removed), the schema version, table
count and latency. Passwords must come from the environment or a secret manager;
they are not accepted on the command line.

## 2. What is implemented

| Area | Implementation |
| --- | --- |
| Driver abstraction | `src/ogx/enterprise/database.py`; prefers `psycopg` 3, accepts `psycopg2`, fails closed with an actionable message if neither is installed |
| Connection pool | bounded LIFO pool (`DEFAULT_POOL_SIZE = 4`), health-discarding on broken connections, thread-safe acquire/release, no unbounded connection growth |
| Versioned migrations | `schema_migrations(version, applied_at, description)`; initial schema v1; PostgreSQL advisory lock serialises concurrent initialisers; SQLite records the same version |
| Portable store | `EnterpriseStore` uses one SQL surface for SQLite and PostgreSQL; placeholders are translated, rows support `row["column"]` and `row[0]`, and SQLite-specific `INSERT OR …` statements were replaced with portable `ON CONFLICT` clauses |
| Readiness | `/ready` checks connectivity and the required table set through `EnterpriseStore.table_names()`, so the probe is backend-independent |
| Queue | `PostgresJobQueue` uses `SELECT … FOR UPDATE SKIP LOCKED` for atomic claims, a transaction-scoped advisory lock for exact capacity/dedupe checks, leases, heartbeats, exponential backoff, dead-letter state and approval-gated requeue |
| Queue factory | `open_job_queue()` selects PostgreSQL from `OGX_DATABASE_URL`; the API `/api/v1/jobs/stats` route uses the same selection |
| CLI | `ogx enterprise backend` reports backend health; `ogx enterprise init` applies migrations |

## 3. Migrations and rollback

`EnterpriseStore.initialize()` is idempotent. On PostgreSQL it:

1. takes `pg_advisory_xact_lock(hashtext('ogx.enterprise.schema_migrations'))`;
2. creates `schema_migrations` if absent;
3. applies the schema only when the recorded version is below `SCHEMA_VERSION`;
4. records version 1 in the same transaction.

A failed schema apply rolls back completely. To add a migration, increment
`SCHEMA_VERSION` in `enterprise/database.py` and add the next idempotent step;
never edit an already-applied migration in place. Before a production upgrade,
take a `pg_dump` and rehearse the migration on a restored copy. A downgrade is a
restore from that dump; the application does not rewrite data in place.

## 4. Queue semantics

* **At-least-once.** `claim()` increments `attempts`; `complete()` and `fail()`
  are fenced by `worker` and `state`.
* **No double claim.** The claim is one `UPDATE … WHERE id = (SELECT … FOR UPDATE
  SKIP LOCKED)` statement. Concurrent workers see disjoint rows.
* **Leases.** `heartbeat()` extends a live lease; a fenced worker gets
  `LeaseLost`. `reap_expired()` returns expired jobs to `pending`, or moves them
  to `dead` when the attempt budget is exhausted.
* **Bounded.** `max_pending` counts pending + leased rows. Enqueue takes an
  advisory lock so two producers cannot both pass the capacity check.
* **Idempotent submission.** A unique `dedupe_key` returns the existing job.
* **Dead letter.** `retry_dead(..., approved=True)` is the only requeue path.

## 5. Verification evidence (2026-11-28)

Environment: Windows, CPython 3.15.2, PostgreSQL 18.7 (isolated temporary
cluster on `127.0.0.1:55432`, `initdb -A trust`; the pre-existing Windows
service was not modified), psycopg2 2.9.13.

```text
$env:OGX_TEST_POSTGRES_URL = "postgresql://postgres@127.0.0.1:55432/ogx_test"
python -m pytest -q tests/test_postgres_backend.py
15 passed in 24.81s
```

The suite creates and drops a throwaway database per session and covers:

* store CRUD, audit events, schema version and health;
* idempotent re-initialisation and completion of a partial legacy schema;
* cross-tenant read/write denial and member isolation;
* service-account tokens, token authentication and idempotency upsert;
* transaction rollback leaving no partial rows;
* bounded pool behaviour and recovery after the server terminates connections;
* queue dedupe, capacity, heartbeat fencing, complete/lease-lost;
* retry/backoff/dead-letter and approval-gated requeue;
* 40 concurrent producers/consumers with no duplicate claims;
* 4 real OS worker processes claiming 24 jobs exactly once;
* lease expiry reaping;
* API `/ready` and a tenant-scoped project route over PostgreSQL.

SQLite remains covered by the existing suites (`tests/test_jobs.py`,
`tests/test_api.py`, `tests/test_enterprise_*`).

## 6. Honest limits

* **No production PostgreSQL deployment is claimed.** The verification used a
  temporary local cluster. Managed PostgreSQL, TLS, HA, failover, backups/PITR,
  connection limits and monitoring are owner/infrastructure decisions.
* **Driver choice is not ratified.** The optional extra recommends psycopg 3;
  the verification ran with psycopg2 because it was already present. See the
  owner decision register.
* **No PITR/WAL archiving.** `ENT-10` PostgreSQL backup remains open.
* **Single-region, single-writer assumptions remain** for the API process. The
  queue is multi-worker safe; the rest of the store uses row transactions, not
  a distributed consensus protocol.
* **No automatic data migration from SQLite to PostgreSQL.** A migration tool
  would need an owner-approved cutover and downtime plan.
