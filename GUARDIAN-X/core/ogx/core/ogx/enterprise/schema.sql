PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS organizations (
  id TEXT PRIMARY KEY, slug TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL,
  created_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY, external_subject TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS memberships (
  organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK(role IN ('owner','admin','developer','auditor','viewer')),
  created_at TEXT NOT NULL, PRIMARY KEY (organization_id, user_id)
);
CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY, organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  name TEXT NOT NULL, provider TEXT NOT NULL DEFAULT 'local', repository_ref TEXT,
  created_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active', UNIQUE(organization_id, name)
);
CREATE INDEX IF NOT EXISTS idx_projects_org ON projects(organization_id);
CREATE TABLE IF NOT EXISTS audit_events (
  id TEXT PRIMARY KEY, organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  actor_user_id TEXT, action TEXT NOT NULL, resource_type TEXT NOT NULL,
  resource_id TEXT, occurred_at TEXT NOT NULL, request_id TEXT, details_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_audit_org_time ON audit_events(organization_id, occurred_at);
CREATE TABLE IF NOT EXISTS licenses (
  id TEXT PRIMARY KEY, organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  edition TEXT NOT NULL, entitlement_json TEXT NOT NULL, valid_from TEXT NOT NULL,
  valid_until TEXT, status TEXT NOT NULL DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS usage_events (
  id TEXT PRIMARY KEY, organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  metric TEXT NOT NULL, quantity REAL NOT NULL CHECK(quantity >= 0), occurred_at TEXT NOT NULL,
  project_id TEXT, metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_usage_org_time ON usage_events(organization_id, occurred_at);
CREATE TABLE IF NOT EXISTS api_credentials (
  id TEXT PRIMARY KEY, organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  key_hash TEXT NOT NULL UNIQUE, label TEXT NOT NULL, scopes_json TEXT NOT NULL,
  created_at TEXT NOT NULL, expires_at TEXT, revoked_at TEXT
);

CREATE TABLE IF NOT EXISTS governance_records (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  record_type TEXT NOT NULL CHECK(record_type IN ('objective','initiative','risk','decision','change','incident','service_metric')),
  title TEXT NOT NULL,
  owner_subject TEXT NOT NULL,
  priority TEXT NOT NULL CHECK(priority IN ('critical','high','medium','low')),
  status TEXT NOT NULL CHECK(status IN ('proposed','planned','open','in_progress','blocked','accepted','mitigated','resolved','done','rejected','cancelled')),
  due_date TEXT,
  details_json TEXT NOT NULL DEFAULT '{}',
  created_by_user_id TEXT NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_governance_records_org_type_status ON governance_records(organization_id, record_type, status);

CREATE TABLE IF NOT EXISTS approval_requests (
  id TEXT PRIMARY KEY,
  operation TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  summary TEXT NOT NULL,
  requester_subject TEXT NOT NULL,
  approver_subject TEXT NOT NULL,
  approver_email TEXT NOT NULL,
  code_salt TEXT NOT NULL,
  code_hash TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('sending','pending','approved','rejected','expired','delivery_failed','executing','executed','execution_failed')),
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  resolved_at TEXT,
  resolved_by TEXT,
  mail_provider TEXT,
  mail_message_id TEXT,
  delivery_error TEXT,
  executed_at TEXT,
  execution_summary TEXT,
  failed_attempts INTEGER NOT NULL DEFAULT 0,
  locked_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_approval_status_expiry ON approval_requests(status, expires_at);
CREATE INDEX IF NOT EXISTS idx_approval_requester_created ON approval_requests(requester_subject, created_at);

CREATE TABLE IF NOT EXISTS approval_events (
  id TEXT PRIMARY KEY,
  request_id TEXT NOT NULL REFERENCES approval_requests(id) ON DELETE CASCADE,
  actor_subject TEXT NOT NULL,
  event_type TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  details_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_approval_events_request_time ON approval_events(request_id, occurred_at);
