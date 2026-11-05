-- Register the off-season dataset and index successfully applied source versions.
INSERT INTO job_sources (key, name, provider_type, repository, file_path, branch, enabled)
VALUES ('simplify-off-season', 'Simplify Off-Season Internships', 'github',
        'https://github.com/SimplifyJobs/Summer2027-Internships', 'README-Off-Season.md', 'dev', true)
ON CONFLICT (key) DO NOTHING;

CREATE INDEX ingestion_runs_success_source_idx ON ingestion_runs(source_id, finished_at DESC)
WHERE status = 'success';
