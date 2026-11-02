-- Retire generated analysis columns from existing installations. No job rows are removed.
ALTER TABLE jobs DROP COLUMN IF EXISTS ai_analysis;
ALTER TABLE jobs DROP COLUMN IF EXISTS ai_status;
ALTER TABLE jobs DROP COLUMN IF EXISTS ai_error;
ALTER TABLE jobs DROP COLUMN IF EXISTS ai_model;
ALTER TABLE jobs DROP COLUMN IF EXISTS ai_description_hash;
ALTER TABLE jobs DROP COLUMN IF EXISTS ai_prompt_version;
ALTER TABLE jobs DROP COLUMN IF EXISTS ai_enriched_at;
UPDATE ingestion_runs SET report = COALESCE(
    (SELECT jsonb_object_agg(key, value) FROM jsonb_each(report) WHERE key NOT LIKE 'ai\_%' ESCAPE '\'),
    '{}'::jsonb
);
DELETE FROM schema_migrations WHERE version = '002_ai_workflow.sql';
