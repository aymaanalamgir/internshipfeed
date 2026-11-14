-- Middle level of the location cascade: province, state, country, or Remote (X).
-- Derived in Python (facets.location_facets); facets RULES_VERSION 3 backfills it.
ALTER TABLE canonical_jobs ADD COLUMN location_areas TEXT[] NOT NULL DEFAULT '{}';
CREATE INDEX canonical_jobs_location_areas_idx ON canonical_jobs USING GIN (location_areas);
