-- Support canonical-merge lookups without duplicating candidate-leading application indexes.
CREATE INDEX IF NOT EXISTS job_applications_job_idx ON job_applications(canonical_job_id);
