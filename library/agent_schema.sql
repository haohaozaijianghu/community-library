ALTER TABLE library_books ADD COLUMN IF NOT EXISTS archived_at timestamptz;
ALTER TABLE library_books ADD COLUMN IF NOT EXISTS revision integer NOT NULL DEFAULT 1;
ALTER TABLE library_jobs ADD COLUMN IF NOT EXISTS source_mode text NOT NULL DEFAULT 'library';
ALTER TABLE library_jobs ADD COLUMN IF NOT EXISTS agent_prompt text;
CREATE INDEX IF NOT EXISTS library_jobs_owner_mode_recent
    ON library_jobs (tenant,owner_id,source_mode,created_at DESC,id DESC);

CREATE TABLE IF NOT EXISTS library_agent_settings (
    tenant text PRIMARY KEY,
    search_titles boolean NOT NULL,
    search_content boolean NOT NULL,
    max_sources integer NOT NULL CHECK (max_sources BETWEEN 1 AND 12),
    CHECK (search_titles OR search_content)
);
ALTER TABLE library_agent_settings ADD COLUMN IF NOT EXISTS prompt text NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS library_passages (
    version_id text NOT NULL REFERENCES library_uploads(id) ON DELETE CASCADE,
    ordinal integer NOT NULL,
    locator text NOT NULL,
    body text NOT NULL,
    PRIMARY KEY (version_id, ordinal)
);
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX IF NOT EXISTS library_passages_search ON library_passages USING gin (lower(body) gin_trgm_ops);
CREATE INDEX IF NOT EXISTS library_books_titles ON library_books USING gin (lower(title) gin_trgm_ops);

INSERT INTO library_passages (version_id,ordinal,locator,body)
SELECT u.id, (p.n - 1)::integer, p.block->>'locator', p.block->>'text'
FROM library_uploads u
CROSS JOIN LATERAL jsonb_array_elements(
    CASE WHEN jsonb_typeof(u.blocks)='array' THEN u.blocks ELSE '[]'::jsonb END
) WITH ORDINALITY AS p(block,n)
WHERE u.status='ready'
  AND NOT EXISTS (SELECT 1 FROM library_passages i WHERE i.version_id=u.id)
ON CONFLICT DO NOTHING;

ALTER TABLE library_jobs ADD COLUMN IF NOT EXISTS conversation_id text REFERENCES library_jobs(id);
CREATE INDEX IF NOT EXISTS library_jobs_conversation_turns
    ON library_jobs (conversation_id,created_at,id) WHERE conversation_id IS NOT NULL;
