-- Canonical filter values: one row may span several cities and terms, so each is an array
-- of normalized labels (see internshipfeed/facets.py). Values are backfilled in Python.
ALTER TABLE canonical_jobs
    ADD COLUMN locations TEXT[] NOT NULL DEFAULT '{}',
    ADD COLUMN terms TEXT[] NOT NULL DEFAULT '{}';
CREATE INDEX canonical_jobs_locations_idx ON canonical_jobs USING GIN (locations);
CREATE INDEX canonical_jobs_terms_idx ON canonical_jobs USING GIN (terms);
