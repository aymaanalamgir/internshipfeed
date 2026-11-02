-- Configured upstream datasets; parser bindings use the stable key.
CREATE TABLE job_sources (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL CHECK (length(trim(name)) > 0),
    provider_type TEXT NOT NULL DEFAULT 'github',
    repository TEXT NOT NULL,
    file_path TEXT NOT NULL,
    branch TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (repository, file_path, branch)
);
INSERT INTO job_sources (key, name, repository, file_path, branch) VALUES
('canadian-tech', 'Canadian Tech Internships', 'https://github.com/negarprh/Canadian-Tech-Internships-2027', 'README.md', 'main'),
('simplify', 'SimplifyJobs Summer 2027', 'https://github.com/SimplifyJobs/Summer2027-Internships', 'README.md', 'dev');
INSERT INTO job_sources (key, name, provider_type, repository, file_path, branch, enabled)
SELECT 'legacy:' || md5(source), source, 'legacy', source, 'README.md', 'unknown', false
FROM (SELECT DISTINCT source FROM source_jobs) sources
WHERE NOT EXISTS (SELECT 1 FROM job_sources configured WHERE configured.repository = sources.source);

CREATE TABLE source_snapshots (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id BIGINT NOT NULL REFERENCES job_sources(id),
    content_hash TEXT NOT NULL,
    commit_sha TEXT,
    raw_content TEXT,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_id, content_hash),
    UNIQUE (source_id, id)
);
-- Legacy snapshots may not have retained files. Never invent their raw content.
INSERT INTO source_snapshots (source_id, content_hash, fetched_at)
SELECT configured.id, old.snapshot_id, min(old.first_seen_at)
FROM source_jobs old JOIN job_sources configured ON configured.repository = old.source
GROUP BY configured.id, old.snapshot_id;

-- Materialize the canonical entity, keeping the public seven-field API contract
-- separate from the additional persisted metadata requested by this schema.
ALTER VIEW canonical_jobs RENAME TO canonical_jobs_legacy;
CREATE TABLE canonical_jobs (
    id BIGINT PRIMARY KEY REFERENCES canonical_job_identities(id),
    company TEXT NOT NULL CHECK (length(trim(company)) > 0),
    title TEXT NOT NULL CHECK (length(trim(title)) > 0),
    location TEXT NOT NULL CHECK (length(trim(location)) > 0),
    application_url TEXT,
    posted_at DATE,
    status TEXT NOT NULL CHECK (status IN ('open', 'closed', 'unknown', 'dead')),
    description TEXT,
    keywords JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(keywords) = 'object'),
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO canonical_jobs (id, company, title, location, application_url, posted_at, status,
                            first_seen_at, last_seen_at, created_at)
SELECT job.*, observed.first_seen_at, observed.last_seen_at, identity.created_at
FROM canonical_jobs_legacy job
JOIN canonical_job_identities identity ON identity.id = job.id
JOIN LATERAL (SELECT min(first_seen_at) AS first_seen_at, max(last_seen_at) AS last_seen_at
              FROM source_jobs WHERE canonical_job_id = job.id) observed ON true;
DROP VIEW canonical_jobs_legacy;
CREATE INDEX canonical_jobs_posted_idx ON canonical_jobs(posted_at DESC NULLS LAST, id DESC);
CREATE INDEX canonical_jobs_status_idx ON canonical_jobs(status);

ALTER TABLE source_jobs RENAME TO source_postings;
ALTER TABLE source_postings ADD COLUMN source_id BIGINT REFERENCES job_sources(id);
UPDATE source_postings p SET source_id = s.id FROM job_sources s WHERE s.repository = p.source;
ALTER TABLE source_postings ALTER COLUMN source_id SET NOT NULL;
ALTER TABLE source_postings RENAME COLUMN snapshot_id TO legacy_snapshot_hash;
ALTER TABLE source_postings ADD COLUMN snapshot_id BIGINT;
UPDATE source_postings p SET snapshot_id = s.id
FROM source_snapshots s WHERE s.source_id = p.source_id AND s.content_hash = p.legacy_snapshot_hash;
ALTER TABLE source_postings ALTER COLUMN snapshot_id SET NOT NULL;
ALTER TABLE source_postings ADD CONSTRAINT source_postings_snapshot_fkey
    FOREIGN KEY (source_id, snapshot_id) REFERENCES source_snapshots(source_id, id);
ALTER TABLE source_postings RENAME COLUMN url TO application_url;
ALTER TABLE source_postings ADD COLUMN raw_company TEXT;
ALTER TABLE source_postings ADD COLUMN raw_title TEXT;
ALTER TABLE source_postings ADD COLUMN raw_location TEXT;
ALTER TABLE source_postings ADD COLUMN raw_url TEXT;
ALTER TABLE source_postings ADD COLUMN raw_posted_at TEXT;
ALTER TABLE source_postings ADD COLUMN raw_row TEXT;
-- Raw legacy values remain NULL until recovered from a retained snapshot.
CREATE TABLE canonical_job_sources (
    canonical_job_id BIGINT NOT NULL REFERENCES canonical_jobs(id),
    source_posting_id BIGINT NOT NULL REFERENCES source_postings(id),
    match_method TEXT NOT NULL CHECK (match_method IN
        ('exact_application_url', 'resolved_application_url', 'source_identity', 'ats_job_id', 'normalized_fields', 'fuzzy_match')),
    match_confidence NUMERIC(4,3) NOT NULL CHECK (match_confidence BETWEEN 0 AND 1),
    matched_url TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (canonical_job_id, source_posting_id),
    UNIQUE (source_posting_id)
);
INSERT INTO canonical_job_sources (canonical_job_id, source_posting_id, match_method, match_confidence, created_at)
SELECT canonical_job_id, id, 'source_identity', 1, first_seen_at
FROM source_postings WHERE canonical_job_id IS NOT NULL;
ALTER TABLE source_postings DROP CONSTRAINT source_jobs_source_external_id_key;
ALTER TABLE source_postings ADD CONSTRAINT source_postings_source_external_key UNIQUE (source_id, external_id);
ALTER TABLE source_postings DROP COLUMN source;
ALTER TABLE source_postings DROP COLUMN legacy_snapshot_hash;
ALTER TABLE source_postings DROP COLUMN canonical_job_id;
CREATE INDEX source_postings_snapshot_idx ON source_postings(snapshot_id);

-- UI workflow summaries remain distinct from per-source operational executions.
ALTER TABLE ingestion_runs RENAME TO ingestion_batches;
CREATE TABLE ingestion_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id BIGINT NOT NULL REFERENCES job_sources(id),
    batch_id UUID REFERENCES ingestion_batches(id),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL CHECK (status IN ('running', 'success', 'partial_failure', 'failed')),
    records_fetched INTEGER NOT NULL DEFAULT 0 CHECK (records_fetched >= 0),
    records_parsed INTEGER NOT NULL DEFAULT 0 CHECK (records_parsed >= 0),
    records_inserted INTEGER NOT NULL DEFAULT 0 CHECK (records_inserted >= 0),
    records_updated INTEGER NOT NULL DEFAULT 0 CHECK (records_updated >= 0),
    records_failed INTEGER NOT NULL DEFAULT 0 CHECK (records_failed >= 0),
    records_skipped INTEGER NOT NULL DEFAULT 0 CHECK (records_skipped >= 0),
    snapshot_id BIGINT,
    error_summary TEXT,
    report JSONB NOT NULL DEFAULT '{}'::jsonb,
    FOREIGN KEY (source_id, snapshot_id) REFERENCES source_snapshots(source_id, id),
    UNIQUE (batch_id, source_id)
);
CREATE INDEX ingestion_runs_source_started_idx ON ingestion_runs(source_id, started_at DESC);
CREATE INDEX ingestion_runs_status_idx ON ingestion_runs(status);

-- Import the source-level history retained inside earlier batch JSON reports.
WITH history AS (
    SELECT b.id AS batch_id, b.started_at, b.finished_at, b.status AS batch_status, source.value AS report
    FROM ingestion_batches b CROSS JOIN LATERAL jsonb_array_elements(
        CASE WHEN jsonb_typeof(b.report->'sources') = 'array' THEN b.report->'sources' ELSE '[]'::jsonb END
    ) source
    UNION ALL
    SELECT id, started_at, finished_at, status, report
    FROM ingestion_batches WHERE jsonb_typeof(report->'sources') IS DISTINCT FROM 'array' AND source <> 'all'
)
INSERT INTO ingestion_runs (source_id, batch_id, started_at, finished_at, status,
    records_fetched, records_parsed, records_inserted, records_updated, records_failed, records_skipped,
    snapshot_id, error_summary, report)
SELECT s.id, h.batch_id, h.started_at, h.finished_at,
    CASE WHEN h.finished_at IS NOT NULL AND h.report->>'status' = 'running' THEN 'failed'
         ELSE COALESCE(h.report->>'status', h.batch_status) END,
    COALESCE((h.report->>'scraped')::int, 0),
    COALESCE((h.report->>'parsed')::int, GREATEST(COALESCE((h.report->>'processed')::int, (h.report->>'scraped')::int, 0) - COALESCE((h.report->>'failed')::int, 0), 0)),
    COALESCE((h.report->>'inserted')::int, 0), COALESCE((h.report->>'updated')::int, 0),
    COALESCE((h.report->>'failed')::int, 0), COALESCE((h.report->>'skipped_duplicates')::int, 0),
    snapshot.id, h.report->>'error', h.report
FROM history h JOIN job_sources s ON s.repository = h.report->>'source'
LEFT JOIN source_snapshots snapshot ON snapshot.source_id = s.id AND snapshot.content_hash = h.report->>'snapshot_id'
ON CONFLICT (batch_id, source_id) DO NOTHING;

CREATE TABLE candidate_profiles (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    target_roles TEXT[] NOT NULL DEFAULT '{}',
    skills TEXT[] NOT NULL DEFAULT '{}',
    preferred_locations TEXT[] NOT NULL DEFAULT '{}',
    preferred_terms TEXT[] NOT NULL DEFAULT '{}',
    graduation_year INTEGER CHECK (graduation_year BETWEEN 1900 AND 2200),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO candidate_profiles DEFAULT VALUES;
CREATE FUNCTION update_profile_timestamp() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at = clock_timestamp(); RETURN NEW; END
$$;
CREATE TRIGGER candidate_profiles_updated BEFORE UPDATE ON candidate_profiles
FOR EACH ROW EXECUTE FUNCTION update_profile_timestamp();
CREATE TABLE resumes (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    candidate_profile_id BIGINT NOT NULL REFERENCES candidate_profiles(id) ON DELETE CASCADE,
    filename TEXT NOT NULL CHECK (length(trim(filename)) > 0),
    extracted_text TEXT,
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX resumes_profile_uploaded_idx ON resumes(candidate_profile_id, uploaded_at DESC);
CREATE TABLE job_interactions (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    candidate_profile_id BIGINT NOT NULL REFERENCES candidate_profiles(id) ON DELETE CASCADE,
    canonical_job_id BIGINT NOT NULL REFERENCES canonical_jobs(id),
    interaction_type TEXT NOT NULL CHECK (interaction_type IN ('view', 'click', 'save', 'dismiss', 'applied')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX job_interactions_profile_time_idx ON job_interactions(candidate_profile_id, created_at DESC);
CREATE INDEX job_interactions_job_time_idx ON job_interactions(canonical_job_id, created_at DESC);
