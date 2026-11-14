-- Coarse location regions (Canada, United States, International) for the two-level filter.
-- Derived from canonical_jobs.locations in Python; facets RULES_VERSION 2 backfills them.
ALTER TABLE canonical_jobs ADD COLUMN location_regions TEXT[] NOT NULL DEFAULT '{}';
CREATE INDEX canonical_jobs_location_regions_idx ON canonical_jobs USING GIN (location_regions);
