-- FeeSink — SQLite Schema (Phase 2)
-- CANON v2026.01.25-LAST-PROVIDER-EVENT-01 (adds accounts.last_provider_event_at_utc, diagnostic only)
-- NOTE: For every SQLite connection MUST run: PRAGMA foreign_keys = ON;

BEGIN;

-- ---------- meta ----------
CREATE TABLE IF NOT EXISTS schema_meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

INSERT OR IGNORE INTO schema_meta(key, value) VALUES ('canon_version', 'v2026.01.25-LAST-PROVIDER-EVENT-01');
INSERT OR IGNORE INTO schema_meta(key, value) VALUES ('timezone', 'UTC');

-- ---------- accounts ----------
CREATE TABLE IF NOT EXISTS accounts (
  account_id     TEXT PRIMARY KEY,
  balance_units  INTEGER NOT NULL CHECK (balance_units >= 0),
  status         TEXT NOT NULL CHECK (status IN ('active','depleted')),
  created_at_utc TEXT NOT NULL,
  updated_at_utc TEXT NOT NULL,

  -- P2 ops-only: last known provider event timestamp for this account (UTC ISO8601)
  last_provider_event_at_utc TEXT NULL
);

CREATE INDEX IF NOT EXISTS idx_accounts_status ON accounts(status);

-- ---------- tokens ----------
CREATE TABLE IF NOT EXISTS tokens (
  token          TEXT PRIMARY KEY,
  account_id     TEXT NOT NULL,
  created_at_utc TEXT NOT NULL,

  FOREIGN KEY (account_id) REFERENCES accounts(account_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_tokens_account_id ON tokens(account_id);

-- ---------- provider_events ----------
CREATE TABLE IF NOT EXISTS provider_events (
  provider          TEXT NOT NULL,
  provider_event_id TEXT NOT NULL,
  event_type        TEXT NULL,
  status            TEXT NOT NULL CHECK (status IN ('received','processed','failed')),
  received_at_utc   TEXT NOT NULL,
  processed_at_utc  TEXT NULL,

  account_id        TEXT NULL,
  credited_units    INTEGER NULL CHECK (credited_units IS NULL OR credited_units >= 0),

  raw_event_json    TEXT NULL,

  raw_body_sha256           TEXT NULL,
  signature_verified_at_utc TEXT NULL,

  FOREIGN KEY (account_id) REFERENCES accounts(account_id) ON DELETE SET NULL,

  UNIQUE (provider, provider_event_id)
);

CREATE INDEX IF NOT EXISTS idx_provider_events_status ON provider_events(status);
CREATE INDEX IF NOT EXISTS idx_provider_events_account_id ON provider_events(account_id);
CREATE INDEX IF NOT EXISTS idx_provider_events_received_at_utc ON provider_events(received_at_utc);

-- ---------- stripe_links ----------
CREATE TABLE IF NOT EXISTS stripe_links (
  stripe_session_id  TEXT PRIMARY KEY,
  stripe_customer_id TEXT UNIQUE NULL,
  account_id         TEXT NOT NULL,
  created_at_utc     TEXT NOT NULL,

  FOREIGN KEY (account_id) REFERENCES accounts(account_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_stripe_links_account_id ON stripe_links(account_id);

-- ---------- endpoints ----------
CREATE TABLE IF NOT EXISTS endpoints (
  endpoint_id       TEXT PRIMARY KEY,
  account_id        TEXT NOT NULL,

  url               TEXT NOT NULL,
  interval_minutes  INTEGER NOT NULL CHECK (interval_minutes > 0),

  enabled           INTEGER NOT NULL CHECK (enabled IN (0,1)),
  paused_reason     TEXT NULL CHECK (paused_reason IN ('manual','depleted')),

  next_check_at_utc TEXT NOT NULL,

  last_check_at_utc TEXT NULL,
  last_result       TEXT NULL CHECK (last_result IN ('ok','fail') OR last_result IS NULL),

  created_at_utc    TEXT NOT NULL,
  updated_at_utc    TEXT NOT NULL,

  FOREIGN KEY (account_id) REFERENCES accounts(account_id) ON DELETE CASCADE,

  CHECK (
    (enabled = 1 AND paused_reason IS NULL) OR
    (enabled = 0 AND paused_reason IS NOT NULL)
  )
);

CREATE INDEX IF NOT EXISTS idx_endpoints_account_id ON endpoints(account_id);
CREATE INDEX IF NOT EXISTS idx_endpoints_due ON endpoints(enabled, next_check_at_utc);

-- ---------- endpoint_leases ----------
CREATE TABLE IF NOT EXISTS endpoint_leases (
  endpoint_id      TEXT PRIMARY KEY,
  lease_token      TEXT NOT NULL,
  lease_until_utc  TEXT NOT NULL,

  created_at_utc   TEXT NOT NULL,
  updated_at_utc   TEXT NOT NULL,

  FOREIGN KEY (endpoint_id) REFERENCES endpoints(endpoint_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_endpoint_leases_until ON endpoint_leases(lease_until_utc);

-- ---------- topups ----------
CREATE TABLE IF NOT EXISTS topups (
  topup_id       TEXT PRIMARY KEY,
  account_id     TEXT NOT NULL,

  tx_hash        TEXT NOT NULL,
  amount_usdt    INTEGER NOT NULL CHECK (amount_usdt >= 0),
  credited_units INTEGER NOT NULL CHECK (credited_units >= 0),

  created_at_utc TEXT NOT NULL,

  FOREIGN KEY (account_id) REFERENCES accounts(account_id) ON DELETE CASCADE,

  UNIQUE (tx_hash)
);

CREATE INDEX IF NOT EXISTS idx_topups_account_created ON topups(account_id, created_at_utc);

-- ---------- check_events ----------
CREATE TABLE IF NOT EXISTS check_events (
  check_id         TEXT PRIMARY KEY,
  account_id       TEXT NOT NULL,
  endpoint_id      TEXT NOT NULL,

  scheduled_at_utc TEXT NOT NULL,
  ts_utc           TEXT NOT NULL,

  result           TEXT NOT NULL CHECK (result IN ('ok','fail')),
  http_status      INTEGER NULL,
  latency_ms       INTEGER NULL CHECK (latency_ms IS NULL OR latency_ms >= 0),
  error_class      TEXT NULL,

  dedup_key        TEXT NOT NULL,

  units_charged    INTEGER NOT NULL CHECK (units_charged = 1),
  created_at_utc   TEXT NOT NULL,

  FOREIGN KEY (account_id) REFERENCES accounts(account_id) ON DELETE CASCADE,
  FOREIGN KEY (endpoint_id) REFERENCES endpoints(endpoint_id) ON DELETE CASCADE,

  UNIQUE (dedup_key)
);

CREATE INDEX IF NOT EXISTS idx_check_events_endpoint_ts ON check_events(endpoint_id, ts_utc);
CREATE INDEX IF NOT EXISTS idx_check_events_account_created ON check_events(account_id, created_at_utc);

COMMIT;
