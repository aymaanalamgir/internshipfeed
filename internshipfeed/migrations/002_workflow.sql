ALTER TABLE ingestion_runs ALTER COLUMN finished_at DROP NOT NULL;
ALTER TABLE ingestion_runs DROP CONSTRAINT IF EXISTS ingestion_runs_status_check;
ALTER TABLE ingestion_runs ADD CONSTRAINT ingestion_runs_status_check
    CHECK (status IN ('running', 'success', 'partial_failure', 'failed'));
CREATE UNIQUE INDEX IF NOT EXISTS ingestion_runs_one_running_source
    ON ingestion_runs (source) WHERE status = 'running';
