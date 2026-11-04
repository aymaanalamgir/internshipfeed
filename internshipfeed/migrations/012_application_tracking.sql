-- Track application progression separately from append-only candidate behavior.
CREATE TABLE job_applications (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    candidate_profile_id BIGINT NOT NULL REFERENCES candidate_profiles(id) ON DELETE CASCADE,
    canonical_job_id BIGINT NOT NULL REFERENCES canonical_jobs(id),
    status TEXT NOT NULL DEFAULT 'applied' CHECK (status IN ('applied', 'interview', 'rejected', 'ghosted', 'offered')),
    applied_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    status_updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (candidate_profile_id, canonical_job_id)
);
CREATE INDEX job_applications_profile_updated_idx ON job_applications(candidate_profile_id, updated_at DESC, id DESC);
CREATE INDEX job_applications_profile_status_updated_idx ON job_applications(candidate_profile_id, status, updated_at DESC, id DESC);
CREATE INDEX job_interactions_profile_job_event_idx ON job_interactions(candidate_profile_id, canonical_job_id, id DESC);

-- Recover the first submission in each currently active apply/unapply cycle.
WITH RECURSIVE lineage AS (
    SELECT id AS original_id, id, merged_into_id FROM canonical_job_identities
    UNION ALL
    SELECT l.original_id, i.id, i.merged_into_id FROM lineage l
    JOIN canonical_job_identities i ON i.id=l.merged_into_id
), history AS (
    SELECT e.candidate_profile_id, l.id AS canonical_job_id, e.id, e.interaction_type, e.created_at
    FROM job_interactions e JOIN lineage l ON l.original_id=e.canonical_job_id
    WHERE l.merged_into_id IS NULL AND e.interaction_type IN ('applied', 'unapplied')
), reversals AS (
    SELECT candidate_profile_id, canonical_job_id, COALESCE(max(id) FILTER (WHERE interaction_type='unapplied'), 0) AS undo_id
    FROM history GROUP BY candidate_profile_id, canonical_job_id
), submissions AS (
    SELECT DISTINCT ON (h.candidate_profile_id, h.canonical_job_id) h.*
    FROM history h JOIN reversals r USING (candidate_profile_id, canonical_job_id)
    WHERE h.interaction_type='applied' AND h.id>r.undo_id
    ORDER BY h.candidate_profile_id, h.canonical_job_id, h.id
)
INSERT INTO job_applications (candidate_profile_id, canonical_job_id, applied_at, status_updated_at, created_at, updated_at)
SELECT candidate_profile_id, canonical_job_id, created_at, created_at, created_at, created_at FROM submissions;
