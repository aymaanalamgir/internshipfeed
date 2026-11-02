-- Preserve the original per-source rows, numeric IDs, and uniqueness constraint.
ALTER TABLE jobs RENAME TO source_jobs;
ALTER TABLE source_jobs RENAME CONSTRAINT jobs_source_external_id_key TO source_jobs_source_external_id_key;
ALTER TABLE source_jobs RENAME CONSTRAINT jobs_pkey TO source_jobs_pkey;
ALTER SEQUENCE jobs_id_seq RENAME TO source_jobs_id_seq;
ALTER INDEX jobs_posted_at_idx RENAME TO source_jobs_posted_at_idx;
ALTER INDEX jobs_status_idx RENAME TO source_jobs_status_idx;

CREATE TABLE canonical_jobs (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    merged_into_id BIGINT REFERENCES canonical_jobs(id),
    CHECK (merged_into_id IS NULL OR merged_into_id < id)
);
CREATE TABLE canonical_job_urls (
    url_key TEXT PRIMARY KEY,
    canonical_url TEXT NOT NULL,
    canonical_job_id BIGINT NOT NULL REFERENCES canonical_jobs(id)
);
CREATE INDEX canonical_job_urls_job_idx ON canonical_job_urls(canonical_job_id);
ALTER TABLE source_jobs ADD COLUMN canonical_job_id BIGINT REFERENCES canonical_jobs(id);
CREATE INDEX source_jobs_canonical_idx ON source_jobs(canonical_job_id);
-- Python URL normalization/backfill runs after pending schema migrations in
-- the same transaction, then makes canonical_job_id NOT NULL before commit.
