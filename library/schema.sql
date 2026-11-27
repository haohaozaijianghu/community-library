CREATE TABLE IF NOT EXISTS library_jobs (
    id text PRIMARY KEY,
    tenant text NOT NULL,
    owner_id text NOT NULL,
    role text NOT NULL,
    idem text NOT NULL,
    query text NOT NULL,
    web_search boolean NOT NULL DEFAULT false,
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    evidence jsonb,
    result jsonb,
    error_code text,
    UNIQUE (tenant, owner_id, idem)
);
CREATE INDEX IF NOT EXISTS library_jobs_pending ON library_jobs (created_at)
    WHERE status IN ('queued', 'running');

CREATE TABLE IF NOT EXISTS library_attempts (
    job_id text NOT NULL REFERENCES library_jobs(id),
    ordinal integer NOT NULL,
    attempt_id text NOT NULL UNIQUE,
    provider text NOT NULL CHECK (provider IN ('primary', 'backup', 'configured')),
    model_id uuid,
    outcome text NOT NULL DEFAULT 'pending',
    PRIMARY KEY (job_id, ordinal)
);

CREATE TABLE IF NOT EXISTS library_books (
    id text PRIMARY KEY,
    tenant text NOT NULL,
    title text NOT NULL,
    author text,
    isbn text,
    shelf text,
    audience text NOT NULL CHECK (audience IN ('reader','staff')),
    active_version text
);
CREATE TABLE IF NOT EXISTS library_uploads (
    id text PRIMARY KEY,
    book_id text NOT NULL REFERENCES library_books(id),
    filename text NOT NULL,
    file_key text NOT NULL,
    checksum text NOT NULL,
    status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','ready','failed')),
    error_code text,
    blocks jsonb,
    rights_basis text,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (book_id, checksum)
);
