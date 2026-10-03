CREATE TABLE IF NOT EXISTS jobs (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source TEXT NOT NULL CHECK (length(trim(source)) > 0),
    external_id TEXT NOT NULL CHECK (length(trim(external_id)) > 0),
    company TEXT NOT NULL CHECK (length(trim(company)) > 0),
    title TEXT NOT NULL CHECK (length(trim(title)) > 0),
    location TEXT NOT NULL CHECK (length(trim(location)) > 0),
    url TEXT,
    last_known_url TEXT,
    posted_at DATE,
    term TEXT,
    status TEXT NOT NULL CHECK (status IN ('open', 'closed', 'unknown')),
    description TEXT,
    keywords JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(keywords) = 'object'),
    enrichment_status TEXT NOT NULL DEFAULT 'not_requested'
        CHECK (enrichment_status IN ('not_requested', 'not_attempted', 'success', 'unavailable', 'skipped', 'failed')),
    enrichment_error TEXT,
    extraction_method TEXT,
    resolved_url TEXT,
    enriched_at TIMESTAMPTZ,
    snapshot_id TEXT NOT NULL,
    source_line INTEGER NOT NULL CHECK (source_line > 0),
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT jobs_source_external_id_key UNIQUE (source, external_id)
);
CREATE INDEX IF NOT EXISTS jobs_posted_at_idx ON jobs (posted_at DESC NULLS LAST, id DESC);
CREATE INDEX IF NOT EXISTS jobs_status_idx ON jobs (status);

CREATE TABLE IF NOT EXISTS ingestion_runs (
    id UUID PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('success', 'partial_failure', 'failed')),
    scraped INTEGER NOT NULL CHECK (scraped >= 0),
    inserted INTEGER NOT NULL CHECK (inserted >= 0),
    updated INTEGER NOT NULL CHECK (updated >= 0),
    skipped_duplicates INTEGER NOT NULL CHECK (skipped_duplicates >= 0),
    failed INTEGER NOT NULL CHECK (failed >= 0),
    report JSONB NOT NULL
);
