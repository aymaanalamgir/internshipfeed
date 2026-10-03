ALTER TABLE jobs ADD COLUMN IF NOT EXISTS ai_analysis JSONB;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS ai_status TEXT NOT NULL DEFAULT 'not_requested'
    CHECK (ai_status IN ('not_requested', 'not_configured', 'success', 'failed', 'skipped'));
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS ai_error TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS ai_model TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS ai_description_hash TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS ai_prompt_version TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS ai_enriched_at TIMESTAMPTZ;
ALTER TABLE ingestion_runs ALTER COLUMN finished_at DROP NOT NULL;
ALTER TABLE ingestion_runs DROP CONSTRAINT IF EXISTS ingestion_runs_status_check;
ALTER TABLE ingestion_runs ADD CONSTRAINT ingestion_runs_status_check
    CHECK (status IN ('running', 'success', 'partial_failure', 'failed'));
CREATE UNIQUE INDEX IF NOT EXISTS ingestion_runs_one_running_source
    ON ingestion_runs (source) WHERE status = 'running';
