CREATE TABLE IF NOT EXISTS library_model_connections (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant text NOT NULL,
    provider text NOT NULL,
    name text NOT NULL,
    model_name text NOT NULL,
    base_url text NOT NULL,
    encrypted_key bytea,
    verified_at timestamptz,
    UNIQUE (tenant, provider, name)
);
ALTER TABLE library_model_connections ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'chat' CHECK (kind IN ('chat','embedding'));
ALTER TABLE library_model_connections ADD COLUMN IF NOT EXISTS dimension integer CHECK (dimension > 0);
ALTER TABLE library_agent_settings ADD COLUMN IF NOT EXISTS answer_model text NOT NULL DEFAULT 'default';
ALTER TABLE library_jobs ADD COLUMN IF NOT EXISTS answer_model text;

CREATE TABLE IF NOT EXISTS library_model_provider_credentials (
    tenant text NOT NULL,
    provider text NOT NULL,
    encrypted_key bytea NOT NULL,
    version integer NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant,provider)
);
CREATE TABLE IF NOT EXISTS library_discovered_models (
    tenant text NOT NULL,
    provider text NOT NULL,
    model_name text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('chat','embedding')),
    PRIMARY KEY (tenant,provider,model_name,kind)
);
