CREATE TABLE IF NOT EXISTS library_login_codes (
    phone text PRIMARY KEY,
    digest text NOT NULL,
    attempts integer NOT NULL DEFAULT 0,
    issued_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS library_web_sessions (
    digest text PRIMARY KEY,
    phone text NOT NULL,
    expires_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS library_accounts (
    tenant_id text NOT NULL,
    user_id text NOT NULL,
    phone text UNIQUE,
    role text NOT NULL CHECK (role IN ('reader','staff')),
    active boolean NOT NULL DEFAULT true,
    version integer NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, user_id)
);

ALTER TABLE library_accounts ALTER COLUMN phone DROP NOT NULL;

CREATE TABLE IF NOT EXISTS library_demo_tokens (
    digest text PRIMARY KEY,
    tenant_id text NOT NULL,
    user_id text NOT NULL,
    FOREIGN KEY (tenant_id,user_id) REFERENCES library_accounts (tenant_id,user_id)
);

CREATE TABLE IF NOT EXISTS library_demo_documents (
    id text PRIMARY KEY,
    tenant_id text NOT NULL,
    audience text NOT NULL CHECK (audience IN ('reader','staff')),
    keywords text[] NOT NULL,
    body text NOT NULL,
    ordinal integer NOT NULL
);
CREATE INDEX IF NOT EXISTS library_demo_documents_scope ON library_demo_documents (tenant_id,audience);

ALTER TABLE library_accounts ADD COLUMN IF NOT EXISTS display_name text NOT NULL DEFAULT '';
ALTER TABLE library_accounts ADD COLUMN IF NOT EXISTS password_hash text;
ALTER TABLE library_accounts ADD COLUMN IF NOT EXISTS password_failures integer NOT NULL DEFAULT 0;
ALTER TABLE library_accounts ADD COLUMN IF NOT EXISTS password_blocked_until timestamptz;

CREATE TABLE IF NOT EXISTS library_profile_codes (
    tenant_id text NOT NULL,
    user_id text NOT NULL,
    purpose text NOT NULL,
    phone text NOT NULL,
    digest text NOT NULL,
    attempts integer NOT NULL DEFAULT 0,
    issued_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, user_id, purpose),
    FOREIGN KEY (tenant_id,user_id) REFERENCES library_accounts (tenant_id,user_id)
);

ALTER TABLE library_accounts ADD COLUMN IF NOT EXISTS username text;
CREATE UNIQUE INDEX IF NOT EXISTS library_accounts_username ON library_accounts (username);

CREATE TABLE IF NOT EXISTS library_registration_codes (
    phone text PRIMARY KEY,
    tenant_id text NOT NULL,
    digest text NOT NULL,
    attempts integer NOT NULL DEFAULT 0,
    issued_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL
);
