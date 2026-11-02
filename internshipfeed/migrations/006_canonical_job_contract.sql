-- Identity lifecycle metadata is separate from the canonical opportunity entity.
-- Renaming retains every ID, merge link, URL alias, and source foreign key.
ALTER TABLE canonical_jobs RENAME TO canonical_job_identities;

-- The canonical entity exposes exactly the seven domain fields. Display values
-- remain derived from source observations rather than duplicated/stale copies.
CREATE VIEW canonical_jobs AS
SELECT identities.id,
       representative.company,
       representative.title,
       representative.location,
       representative.url AS application_url,
       (SELECT min(observation.posted_at)
        FROM source_jobs observation
        WHERE observation.canonical_job_id = identities.id) AS posted_at,
       representative.status
FROM canonical_job_identities identities
JOIN LATERAL (
    SELECT company, title, location, url, status
    FROM source_jobs observation
    WHERE observation.canonical_job_id = identities.id
    ORDER BY CASE status WHEN 'open' THEN 0 WHEN 'unknown' THEN 1 WHEN 'closed' THEN 2 ELSE 3 END,
             posted_at IS NULL, id
    LIMIT 1
) representative ON true
WHERE identities.merged_into_id IS NULL;
