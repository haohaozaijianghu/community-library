CREATE TABLE IF NOT EXISTS library_community_posts (
    tenant_id text NOT NULL,
    id uuid NOT NULL,
    kind text NOT NULL CHECK (kind IN ('notice', 'activity')),
    title text NOT NULL,
    body text NOT NULL,
    state text NOT NULL DEFAULT 'draft' CHECK (state IN ('draft', 'published', 'withdrawn')),
    version integer NOT NULL DEFAULT 1,
    updated_at timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz,
    PRIMARY KEY (tenant_id, id)
);

CREATE INDEX IF NOT EXISTS library_community_posts_published
    ON library_community_posts (tenant_id, published_at DESC, id)
    WHERE state = 'published';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid='library_community_posts'::regclass AND conname='library_community_posts_content_kind'
    ) THEN
        ALTER TABLE library_community_posts DROP CONSTRAINT IF EXISTS library_community_posts_kind_check;
        ALTER TABLE library_community_posts ADD CONSTRAINT library_community_posts_content_kind
            CHECK (kind IN ('notice', 'activity', 'guide', 'faq', 'handbook'));
    END IF;
END
$$;
ALTER TABLE library_community_posts ADD COLUMN IF NOT EXISTS sort_order integer NOT NULL DEFAULT 0;
ALTER TABLE library_community_posts ADD COLUMN IF NOT EXISTS keywords text[] NOT NULL DEFAULT '{}';
ALTER TABLE library_community_posts ADD COLUMN IF NOT EXISTS featured boolean NOT NULL DEFAULT false;
ALTER TABLE library_community_posts ADD COLUMN IF NOT EXISTS starts_at timestamptz;
ALTER TABLE library_community_posts ADD COLUMN IF NOT EXISTS location text;
ALTER TABLE library_community_posts ADD COLUMN IF NOT EXISTS capacity integer;

CREATE TABLE IF NOT EXISTS library_portal_settings (
    tenant_id text PRIMARY KEY,
    title text NOT NULL,
    intro text NOT NULL,
    shortcuts text[] NOT NULL,
    external_notices boolean NOT NULL DEFAULT true,
    registration_enabled boolean NOT NULL DEFAULT true,
    otp_login_enabled boolean NOT NULL DEFAULT true,
    version integer NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS library_activity_enrollments (
    tenant_id text NOT NULL,
    post_id uuid NOT NULL,
    user_id text NOT NULL,
    enrolled_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id,post_id,user_id),
    FOREIGN KEY (tenant_id,post_id) REFERENCES library_community_posts (tenant_id,id),
    FOREIGN KEY (tenant_id,user_id) REFERENCES library_accounts (tenant_id,user_id)
);

ALTER TABLE library_books ADD COLUMN IF NOT EXISTS category text;

CREATE TABLE IF NOT EXISTS library_help_requests (
    job_id text PRIMARY KEY REFERENCES library_jobs(id),
    tenant_id text NOT NULL,
    user_id text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    reply text,
    actor_id text,
    answered_at timestamptz,
    FOREIGN KEY (tenant_id,user_id) REFERENCES library_accounts (tenant_id,user_id)
);
CREATE INDEX IF NOT EXISTS library_help_requests_tenant ON library_help_requests (tenant_id, created_at DESC);

CREATE TABLE IF NOT EXISTS library_password_reset_codes (
    phone text PRIMARY KEY,
    digest text NOT NULL,
    attempts integer NOT NULL DEFAULT 0,
    issued_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL
);

ALTER TABLE library_community_posts ADD COLUMN IF NOT EXISTS source_id text;
CREATE UNIQUE INDEX IF NOT EXISTS library_community_seed ON library_community_posts (tenant_id,source_id);
ALTER TABLE library_portal_settings ADD COLUMN IF NOT EXISTS notice_feed text;

CREATE TABLE IF NOT EXISTS library_sms_usage (
    minute timestamptz PRIMARY KEY,
    sends integer NOT NULL
);
