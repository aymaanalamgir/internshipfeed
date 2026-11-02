# PostgreSQL architecture

The nine application tables are `job_sources`, `ingestion_runs`,
`source_snapshots`, `source_postings`, `canonical_jobs`,
`canonical_job_sources`, `candidate_profiles`, `resumes`, and `job_interactions`.

```mermaid
erDiagram
    JOB_SOURCES ||--o{ INGESTION_RUNS : executes
    JOB_SOURCES ||--o{ SOURCE_SNAPSHOTS : versions
    JOB_SOURCES ||--o{ SOURCE_POSTINGS : publishes
    SOURCE_SNAPSHOTS ||--o{ SOURCE_POSTINGS : observed_in
    SOURCE_SNAPSHOTS o|--o{ INGESTION_RUNS : captured_by
    SOURCE_POSTINGS ||--o| CANONICAL_JOB_SOURCES : reconciled_by
    CANONICAL_JOBS ||--o{ CANONICAL_JOB_SOURCES : groups
    CANDIDATE_PROFILES ||--o{ RESUMES : has
    CANDIDATE_PROFILES ||--o{ JOB_INTERACTIONS : generates
    CANONICAL_JOBS ||--o{ JOB_INTERACTIONS : receives
```

## Source configuration

`job_sources` contains:

```text
id bigint PK
key text UNIQUE
name text
provider_type text
repository text
file_path text
branch text
enabled boolean
created_at timestamptz
```

`repository` is the full upstream repository URL. `key` binds a dataset to its
registered parser. `(repository, file_path, branch)` is unique. The initial rows
are Canadian Tech Internships (`main/README.md`) and SimplifyJobs Summer 2027
(`dev/README.md`). Ingestion reads name, repository, branch, file path, and enabled
state from PostgreSQL; the code registry supplies the parser implementation.
Disabling a source prevents future ingestion without deleting its past records.
Feed source filters use dataset IDs, so separate files/branches in the same
repository keep independent identities and filter choices.

Legacy datasets outside the registry are preserved as disabled `legacy` sources.
Adding a new parser requires a registry entry as well as source configuration.

## Dataset versions

`source_snapshots` contains:

```text
id bigint PK
source_id bigint FK -> job_sources
content_hash text
commit_sha text NULL
raw_content text NULL
fetched_at timestamptz
UNIQUE(source_id, content_hash)
```

New ingestion stores the complete downloaded UTF-8 README before parsing.
`content_hash` is its SHA-256 digest. Re-fetching identical content reuses the
snapshot ID and first `fetched_at`, so snapshots are not duplicated. Different
sources have separate snapshot identities even for identical content.

The current raw-README fetcher does not supply a verified commit SHA, so
`commit_sha` stays null unless a caller supplies one. A branch URL or ETag is not
misrepresented as a commit SHA. An ingestion run's timestamp records each new
execution even when its snapshot is reused.

## Source postings

`source_postings` contains all requested source-specific fields:

```text
id bigint PK
source_id bigint FK -> job_sources
snapshot_id bigint FK -> source_snapshots
external_id text
raw_company text NULL
raw_title text NULL
raw_location text NULL
raw_url text NULL
raw_posted_at text NULL
company text
title text
location text
application_url text NULL
posted_at date NULL
status text
first_seen_at timestamptz
last_seen_at timestamptz
UNIQUE(source_id, external_id)
```

Additional diagnostic columns retain `raw_row`, `source_line`, `term`, the last
known application URL, description/keyword extraction results and outcomes, and
link-check timestamps. They support the existing detail UI and retries.

A composite foreign key `(source_id, snapshot_id)` ensures the snapshot belongs
to the posting's own source. Raw cells preserve source formatting and continuation
markers; normalized fields contain resolved company names, readable locations,
and the actual application URL. Relative ages remain in `raw_posted_at`; exact
posting dates remain null when the source does not provide them.

A posting stores its latest observation's snapshot ID. Older dataset versions
remain replayable from `source_snapshots`; this is not a full per-posting revision
history table.

## Canonical opportunities

`canonical_jobs` is a **stored table**, not a view:

```text
id bigint PK
company text
title text
location text
application_url text NULL
posted_at date NULL
status text
description text NULL
keywords jsonb
first_seen_at timestamptz
last_seen_at timestamptz
created_at timestamptz
updated_at timestamptz
```

Canonical fields are refreshed transactionally from linked source observations
when a source posting is saved or groups are merged. Active source observations
win over closed/dead observations. Exact-date metadata is preferred, with stable
source IDs breaking ties. The earliest known exact posting date, latest available
active description, and combined keyword lists are retained. `updated_at` changes
when canonical content changes; observation timestamps remain separate.

The public `CanonicalJob` model and `/api/jobs` responses retain their seven-field
contract. `StoredCanonicalJob` describes the full persisted row, while `JobDetails`
provides the UI's source and enrichment context.

## Explainable reconciliation

`canonical_job_sources` is the mapping table (the `job_sources_map` in the sketch):

```text
canonical_job_id bigint FK -> canonical_jobs
source_posting_id bigint FK -> source_postings
match_method text
match_confidence numeric(4,3) CHECK 0 <= value <= 1
matched_url text NULL
created_at timestamptz
PRIMARY KEY(canonical_job_id, source_posting_id)
UNIQUE(source_posting_id)
```

The unique posting constraint records one current canonical assignment per source
posting. Source IDs and `(source_id, external_id)` remain independent of canonical
IDs. The mapping's creation timestamp is retained unless the decision changes.

Implemented decisions are:

- `exact_application_url`: normalized application URL evidence.
- `resolved_application_url`: additional URL evidence from a successfully read job page.
- `source_identity`: preserve an existing within-source assignment or create an independent opportunity when URL evidence is absent.

Confidence `1.000` denotes a deterministic decision under these rules, not a
calibrated probability or title-based inference. `matched_url` records the URL
evidence when available. The schema reserves `ats_job_id`, `normalized_fields`,
and `fuzzy_match` for later implementations; they are not used by current ingestion.

URL normalization removes known tracking parameters and transport differences
while preserving job identifiers, path case, and meaningful hash routes. Generic
careers/search pages and missing URLs are not matching evidence. Similar titles
alone never merge records. A source URL correction moves only the affected posting.

Private support tables retain canonical identity allocation/merge redirects and
normalized URL aliases. Old merged IDs still resolve to the surviving canonical
job. Those tables avoid rewriting source identities or historical interactions.

## Per-source operational records

`ingestion_runs` contains:

```text
id uuid PK
source_id bigint FK -> job_sources
started_at timestamptz
finished_at timestamptz NULL
status text
records_fetched integer
records_parsed integer
records_inserted integer
records_updated integer
records_failed integer
snapshot_id bigint FK -> source_snapshots NULL
error_summary text NULL
```

Additional fields are `records_skipped`, `batch_id`, and a JSON diagnostic report.
A snapshot foreign key also checks source ownership. A parse failure still retains
the downloaded snapshot and a failed execution with an error summary. When parsing
fails before a row count is available, record counts are zero rather than guessed.
For successful parsing, `records_fetched` is the source record count and
`records_parsed` counts records successfully normalized into `JobListing`.

Each source has its own execution row. A separate `ingestion_batches` support table
tracks a UI sync encompassing multiple sources; one failed source does not roll
back other sources. Batch endpoints power the overall progress UI, while run
endpoints expose these per-source records.

`records_fetched = records_inserted + records_updated + records_skipped + records_failed`
for executions with known row counts. Description-fetch failures are separately
reported and do not fail a valid posting. One posting, its mapping, and refreshed
canonical metadata commit together. Partial row failures preserve committed work.

## Development candidate data

`candidate_profiles` contains `id`, `target_roles`, `skills`,
`preferred_locations`, `preferred_terms`, `graduation_year`, `created_at`, and
`updated_at`. Preference/skill fields use native PostgreSQL text arrays. The
migration creates one empty development profile, and a trigger maintains its
update timestamp. No authentication schema is required for this stage.

Candidate profile persistence + FastAPI profile API is implemented:

- `GET /api/profile` returns the existing development row with all stored profile fields.
- `PUT /api/profile` replaces all five editable fields and returns the stored row.

`development_profile()` selects the lowest-ID row, preserving the existing
single-profile convention. `update_development_profile()` updates that same row
with `UPDATE ... RETURNING *`; it never inserts a profile. ID and `created_at`
remain unchanged. The migration's `candidate_profiles_updated` trigger sets
`updated_at` using `clock_timestamp()`, including for repeated identical PUTs.
psycopg passes Python lists directly as PostgreSQL `text[]`, including empty lists.

`CandidateProfileUpdate` requires every editable field. Its lists must contain
strings; trimming removes surrounding whitespace, blank items disappear, and
exact case-sensitive duplicates retain the first occurrence's spelling and order.
Graduation year is required but nullable, with strict integer values between 1900
and 2200, matching the existing database range. Client-supplied IDs, timestamps,
and other extra fields are rejected with HTTP 422, as are incomplete/invalid
requests. `CandidateProfile` is the response model including stored timestamps.
PUT follows the existing local-only, same-origin mutation policy.

Missing profiles return HTTP 503 with an initialization message; GET/PUT never
silently reseed. Migration tracking remains responsible for the single initial
insert, and repeated initialization preserves both the row count and saved values.
Storage errors use the API's existing HTTP 503 handler without returning PostgreSQL
exception details. No schema migration was needed for this API slice.

`resumes` contains `id`, `candidate_profile_id`, `filename`, `extracted_text`, and
`uploaded_at`. It stores text/metadata only: there is no raw binary column or
permanent file path. Deleting a profile cascades to its résumé records and interactions.

`POST /api/resumes` implements résumé PDF upload → local `pdfplumber` text
extraction → PostgreSQL persistence. It uses the existing database connection
dependency, `development_profile()` lookup, and `save_resume_text()` insert. The
caller cannot select a profile; every upload appends a new history row for the
lowest-ID development profile. The profile itself remains unchanged. No migration
was needed. Metadata and skill-suggestion reads are scoped to this candidate;
there is no full-text résumé history/management endpoint.

The multipart field is `file` (`UploadFile`). A successful request returns HTTP 201
with a `Resume` response: `id`, `candidate_profile_id`, `filename`, `extracted_text`,
and `uploaded_at`. Only the client filename's basename is retained, not directory
components. No raw PDF bytes, base64 data, temporary paths, or inferred candidate
data are persisted or sent to another service.

Validation uses three checks: a case-insensitive `.pdf` filename, a compatible
MIME type, and actual PDF parsing. `application/pdf`, unspecified MIME, and generic
`application/octet-stream` are permitted; parsing is always required. Other
extensions/MIME types receive HTTP 415. Files are limited to **5 MiB (5,242,880
bytes)**, checked before extraction with a bounded read and the upload's reported
size; larger files receive HTTP 413. FastAPI may spool multipart data to temporary
disk before the endpoint runs; the handler limits its own memory read and parsing.

`extract_pdf_text()` opens the document locally, enforces **10 pages** before
extracting text, and reads pages in order. Pages returning `None` or blank text do
not discard other pages. Cleanup normalizes CRLF/CR to LF, strips trailing spaces
and outer whitespace, collapses runs of blank lines, and removes non-readable NUL
characters (unsupported by PostgreSQL text). It preserves line order, indentation,
case, and technical symbols. The API rejects corrupt/unreadable PDFs, excess
pages, and effectively empty extraction with HTTP 422. Scanned/image-only PDFs
are unsupported; no OCR, AI, skill extraction, or profile updates are performed.

The PDF context and upload are closed on success and failure. Temporary upload
files are not retained. Document contents and parser diagnostics are not logged;
insert failures also use a generic HTTP 503 without logging PostgreSQL diagnostics
that might contain rejected row text. Missing profiles return HTTP 503 without
creating a candidate. Uploads follow the existing local-only mutation policy.

`job_interactions` contains `id`, `candidate_profile_id`, `canonical_job_id`,
`interaction_type`, and `created_at`. Allowed types are `view`, `click`, `save`,
`unsave`, `dismiss`, `undismiss`, `applied`, and `unapplied`. Every event inserts a new row; two views create two
records. There is no uniqueness constraint that overwrites interaction history.

`POST /api/jobs/{job_id}/interactions` exposes click/save/unsave/dismiss/undismiss/applied/unapplied for the
development profile; view stays internal. An explicit request model rejects extra
fields and invalid actions. `record_interaction()` resolves merged IDs and checks
the stored canonical job exists before insertion. Existing inactive canonical
records can receive events too, so historical application actions remain valid.

`GET /api/interactions` derives candidate-specific effective state. A recursive
query follows canonical identity merge redirects from each historical event ID,
then selects the latest event by ID per signal family using `DISTINCT ON`.
Save/Unsave, Applied/Unapplied, and Dismiss/Undismiss are paired families; their
latest action sets the flag. Active events supply `saved_at`, `applied_at`, and
`dismissed_at`, with null after the respective reversal.
`max(created_at)` across all events gives the last interaction time;
`max(id)` gives a `last_event_id` snapshot
cursor. These values are derived, not additional stored columns. Repeated
events do not duplicate state rows. View/click-only jobs have all three flags
false. Never-interacted jobs are absent and the frontend defaults their flags
to false. History is not rewritten or deleted when jobs merge.

Saved, Applied, and Dismissed are reversible without deleting history. Repeated explicit
actions are state setters rather than blind toggles, so duplicate requests do not
accidentally flip flags. Event ID order resolves tied/backwards timestamps.
Recommended discovery requires not dismissed and not applied. Saved requires
saved and not dismissed, while Applied requires applied regardless of the other
flags. Thus a saved/applied job remains in both history views; a saved/dismissed
job is absent from discovery and Saved but appears in Dismissed.
Restore clears only dismissal and cancels its negative behavioral signal. Interaction state does not affect matching
profile scores or canonical job fields. Migrations `008_reversible_interactions.sql`
and `011_reversible_dismissals.sql` only extend the event-type check constraint. No state table/columns were added.

Repository helpers expose the development profile, append interaction events,
and save extracted résumé text. The React Profile drawer consumes GET/PUT profile,
POST résumé, and metadata-only `GET /api/resumes/latest`. That read selects the
development candidate's latest filename/date without reading extracted text.
All views now share React; `/` is Recommended and `/feed` aliases All listings.
See [frontend.md](frontend.md) for build and development details. Résumé-to-profile
suggestions and authentication are future work.

## On-request recommendations

Candidate Profile + Canonical Jobs → Deterministic Matching → Recommendations API
uses `matching.py` for unchanged profile fit, `behavioral.py` for bounded
interaction-derived affinity, and `recommendations.py` for 75/25 composition.
The API uses
`candidate_job_details()` to combine `interaction_states()` with existing canonical
job/source metadata loading. Recommended, Saved, Applied, and Dismissed share this query
layer and the same pure matcher. Normal `/api/jobs` and `/api/feed` discovery also
exclude dismissed/applied jobs; raw repository helpers remain available for
ingestion and diagnostics.

Matching is rule-based, string-based, skills-based, and explainable. The fixed
weights are role 40, skills 30, location 20, and term 10. Results contain the same
seven-field canonical job plus component scores, total, matched skills,
evidence reasons, and `interaction_state`. Recommended adds `profile_score`
(equal to the unchanged `score`), `behavior_score`, `final_score`, and at most two
`behavior_reasons`. Optional `min_score` still filters profile fit in 0–100.
Scored collections also attach existing `term` and `keywords` metadata for cards;
these are response additions rather than new stored fields or scoring inputs.
Closed/dead canonical jobs are excluded; unknown status follows feed visibility.

Scores are calculated in Python on each request and never written to PostgreSQL.
There is no score column or recommendations table. Profile changes affect the next
request without a backfill. Résumé text, interaction events, graduation year,
descriptions, and posting dates do not contribute profile points. A separate
behavior score uses interaction/job metadata; final rank is 75% profile + 25%
behavior. Posting date and ID only break ties. See [matching.md](matching.md) and
[behavioral.md](behavioral.md) for exact rules and limitations.

`GET /api/saved`, `GET /api/applied`, and `GET /api/dismissed` retain profile-scored cards without the
Recommended-only behavioral fields. Saved
is newest-save first; Applied is newest-applied first, with ID descending for
ties. Later clicks/views do not reorder application history. These collections
retain inactive and unlinked canonical records. `list_job_details()` accepts
selected/excluded ID sets and reads observations only for selected canonical
jobs; empty history collections avoid unnecessary metadata reads. No raw events
are transferred to Python for state replay.

`behavior_history()` uses the same candidate-scoped merge lineage and latest
signal-family projection. Unsaved/unmarked signals contribute no save/application
weight until reactivated. Undismiss cancels the corresponding dismissal weight;
click evidence remains independent. It then calls
the existing bulk metadata loader, including inactive/unlinked history. This is
three queries (grouped history, canonical metadata, observations) rather than one
query per event/job. Python builds a temporary bounded affinity object; neither
preferences nor recommendation scores are written to PostgreSQL. Repeated signals
are capped across merged aliases; `view` is excluded. Existing indexes suffice,
with no migration or new state/cache table.

`record_interaction_with_state()` reuses the existing insert helper and returns
the SQL-derived snapshot in one transaction. It uses the existing reconciliation
advisory lock so canonical merges cannot change the root between write and receipt.

## Constraints and indexes

`job_notifications` stores provider-accepted email history: candidate/job foreign
keys, `channel='email'`, `provider_message_id`, and acceptance time (`sent_at`). The
unique `(candidate_profile_id, canonical_job_id, channel)` constraint prevents
duplicate ledger entries. Selection resolves historical canonical redirects so
notification aliases remain deduplicated after merges. No recipient addresses or
credentials are stored in this table or exposed through frontend APIs.

Migration `009_job_notifications.sql` created the ledger;
`010_email_notifications.sql` upgrades its provider column and channel constraint
while preserving earlier audit rows. All runtime delivery uses email. Exact batch discovery IDs
are stored in the existing `ingestion_batches.report.new_canonical_job_ids`; no
recommendation scores or behavioral profiles are persisted. See
[daily-refresh.md](daily-refresh.md) for orchestration, transaction boundaries,
partial failures, and the external provider acceptance/recording limitation.

The schema enforces source snapshot uniqueness, within-source posting uniqueness,
snapshot ownership, one canonical assignment per posting, valid match confidence,
valid statuses, event types, and profile/job foreign keys. Indexes support source
posting lookups, source execution history, canonical dates/statuses, mapping joins,
and interactions by profile/job and time.
The existing `job_interactions_profile_time_idx(candidate_profile_id, created_at
DESC)`, `job_interactions_job_time_idx(canonical_job_id, created_at DESC)`, and ID
primary key were verified in PostgreSQL. Candidate-scoped aggregation and ID-based
canonical lookups are supported without a new index or migration.

## Migration and legacy data

`007_relational_architecture.sql` upgrades existing data in one transaction:

1. Registers upstream source datasets.
2. Converts hash-only references into deduplicated snapshot rows.
3. Moves `source_jobs` to `source_postings`, preserving IDs and observations.
4. Copies canonical assignments into `canonical_job_sources`.
5. Materializes canonical job descriptions, keywords, and timestamps.
6. Keeps earlier aggregate run records as `ingestion_batches` and imports their source-level history into `ingestion_runs`.
7. Creates the development profile, résumé metadata, and event tables.

A checked migration hook restores retained snapshot files only when their hashes
match, recovers raw source cells where possible, and fills reconciliation metadata.
If an old snapshot file is unavailable, legacy `raw_content`/raw cell fields remain
null rather than inventing upstream facts. Fresh ingestion always stores raw data.
Reinitialization does not duplicate sources, snapshots, postings, mappings, or the
seed profile. Existing source IDs, external IDs, canonical IDs, and observation
timestamps are preserved.

## Useful queries

```sql
-- Every source identity behind a canonical opportunity.
SELECT c.id AS canonical_job_id, s.name, p.id AS source_posting_id,
       p.external_id, m.match_method, m.match_confidence, m.matched_url
FROM canonical_jobs c
JOIN canonical_job_sources m ON m.canonical_job_id = c.id
JOIN source_postings p ON p.id = m.source_posting_id
JOIN job_sources s ON s.id = p.source_id
WHERE c.id = 420;

-- Per-source ingestion health.
SELECT s.name, r.started_at, r.status, r.records_fetched, r.records_parsed,
       r.records_inserted, r.records_updated, r.records_failed, r.error_summary
FROM ingestion_runs r JOIN job_sources s ON s.id = r.source_id
ORDER BY r.started_at DESC;
```
