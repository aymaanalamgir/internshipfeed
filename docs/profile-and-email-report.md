# Profile suggestions and daily email implementation report

## Implemented behavior

Graduation is now a select with Not specified and 2026–2030. The empty choice
saves `null`; other choices save integers. Preferred terms use removable chips
and one add select for Winter/Summer/Fall in those five years. Duplicates are
blocked. Existing legacy terms and graduation years outside the new choices
remain intact until edited. Roles, skills, and locations keep editable tags.
The generic Enter instruction is screen-reader-only.

Résumé suggestions use the existing `enrich.VOCABULARY`, matching aliases, and
technical-concept allowlist, with React Native added to the shared vocabulary.
Aliases include Postgres, K8s, NodeJS, Google Cloud, Amazon Web Services, JS, TS,
and dotnet. Matching uses technical token boundaries, multiword separators,
longest overlapping phrase precedence, canonical labels, deduplication, and
stable normalized-key ordering. JavaScript/Java, C++/C#/C, and React Native/React
remain distinct. Ambiguous bare C/R/Go retain case-sensitive vocabulary rules;
R&D/R/S and generic résumé prose are excluded. This controlled list is not a
complete inventory of all technologies and does not infer missing skills.

`GET /api/resumes/{resume_id}/skill-suggestions` returns `resume_id`, `skills`,
and normalization `aliases`. The development candidate must own the résumé;
missing/foreign rows return 404. The API never returns extracted text through
this endpoint. Suggestion failure returns a sanitized 503 independently of the
already-committed upload.

Profile loads suggestions for its latest successful résumé and rejects/cancels
stale requests. Normalized selected and pending skills are filtered out. A
suggestion click or Add all changes only the draft; the existing complete
`PUT /api/profile` runs only on Save changes. A new PDF replaces suggestions,
preserving saved and draft skills. No other profile fields are inferred.

The one-shot `python -m internshipfeed.daily_refresh` remains independent of
résumés. External host cron supplies the daily cadence. It calls existing
`run_ingestion()` for all enabled sources. Under the sync lock, ingestion
captures known canonical identity IDs before sources and surviving canonical
roots afterward; absent root IDs become the exact retained
`new_canonical_job_ids`. Updates and source duplicates do not become new jobs.
This avoids inherited/backdated `first_seen_at` errors and never uses `posted_at`.

The coordinator resolves already-notified merge aliases, reuses
`db.candidate_job_details()` workflow eligibility, builds current behavior with
`build_behavior_profile(db.behavior_history(...))`, and calls the same
`rank_recommendations()` as Recommended. It filters on final score (default ≥60)
and takes at most five sendable jobs in existing rank order. Closed/dead,
dismissed, and applied jobs are excluded; a résumé is not required.

The official `resend>=2.50.1,<3` SDK receives one HTML/plain-text digest. It shows
company, title, final score, location, available term, matched skills, and Apply
links, using warm white/ink/orange with thin borders and technical tags. HTML is
escaped; unsafe application URLs are skipped. Internal IDs, score components,
profile data, interaction history, and résumé text are absent from the email.

Only a valid Resend acceptance receipt triggers atomic ledger insertion, with no
SQL transaction held during the network request. Uniqueness is candidate/job/
channel, and selection follows canonical redirects. Migration 010 preserves
historical audit rows from the earlier ledger while using `provider_message_id`
and email at runtime. The provider receives a stable payload idempotency key;
PostgreSQL supplies permanent deduplication after acceptance. This does not
promise exactly-once delivery through crashes/timeouts or changed retry payloads.

Partial source failure retains successful jobs and may send, but exits 1. Full
failure sends nothing. Provider failure keeps ingestion and writes no notification
rows. Empty selection sends nothing. Ledger failure after acceptance exits 1 and
instructs checking Resend before retrying. Provider errors are sanitized.

## Setup, preview, and schedule

Install updated dependencies using `python -m pip install -e '.[test]'`. The SDK
has been installed in this workspace; Python 3.9 uses urllib3 1.26 for this Mac's
LibreSSL compatibility. The frontend production build is updated.

Configure the empty placeholders in `.env`:

```dotenv
DAILY_NOTIFICATION_CHANNEL=email
RESEND_API_KEY=
DAILY_EMAIL_FROM=
DAILY_EMAIL_TO=
DAILY_EMAIL_MIN_SCORE=60
DAILY_EMAIL_TOP_N=5
APP_BASE_URL=
```

Sender/domain verification belongs to the Resend account. Recipient is one
configured address. `APP_BASE_URL` is optional. Current `.env` has no email
credential/sender/recipient values. No credentials or actual addresses were added.

```sh
.venv/bin/python -m internshipfeed.daily_refresh --dry-run
```

This ingests/persists source/job changes and prints subject, plain text, HTML,
and a JSON summary, without contacting Resend or recording notifications. Retry
or preview a retained batch with `--no-ingest --batch-id BATCH_UUID` and optional
`--dry-run`; a new ingestion does not automatically replay an old preview.

```cron
0 9 * * * cd /absolute/path/to/internshipfeed && /absolute/path/to/internshipfeed/.venv/bin/python -m internshipfeed.daily_refresh >> /absolute/path/to/internshipfeed/data/daily-refresh.log 2>&1
```

Paths, log directory, and host timezone must be configured for deployment. This
runs at 09:00 in the cron host's zone, nominally every 24 hours; DST changes elapsed
time. The host and PostgreSQL must stay available. The cron entry is a template;
no system schedule was installed. See [full daily setup](daily-refresh.md).

## Verification performed

- Backend: **207 tests passed**, including real PostgreSQL tests in disposable
  schemas on the existing server; no skips in that configured run.
- Frontend: **38 tests passed**.
- TypeScript checking and Vite production build passed.
- Full browser/API/PostgreSQL workflow passed using Brave/Chromium, including
  Recommended/Saved/Applied, reversals, ranking, navigation, and directory behavior.
- Profile browser checks covered all year/term options, null and year persistence,
  multiple terms/removal/duplicate prevention, real generated PDF extraction,
  normalized existing/pending skill filtering, suggestion click without a PUT,
  explicit save and reload, upload-success/suggestion-failure isolation and retry,
  latest résumé replacement preserving selected/draft skills, upload failures,
  keyboard focus, Escape, desktop and 390px mobile layouts.
- Email checker performed real two-source fixture ingestion, direct workflow and
  CLI batch dry runs, checked ordering against `/api/recommendations`, verified
  subject/body and matched skills, and rendered desktop/mobile email screenshots.
  It asserted zero provider calls and zero notification rows.
- Historical-ledger migration test verified audit rows survive and that the prior
  channel does not suppress email notifications.
- `git diff --check` passed. Existing demo `/` and `/api/health` on port 8004 return
  HTTP 200. Temporary browser server/schema were removed; live profile and résumé
  history were not modified by these checks.

Reproduce:

```sh
.venv/bin/python scripts/test_with_existing_postgres.py
npm --prefix frontend test
npm --prefix frontend run build
.venv/bin/python scripts/check_recommendations_ui.py --browser-executable /path/to/chromium
.venv/bin/python scripts/check_daily_email.py --browser-executable /path/to/chromium
```

Ignored previews are in `data/screenshots/`: `profile-desktop.png`,
`profile-mobile.png`, `profile-mobile-bottom.png`, `daily-email-preview.html`,
`daily-email-preview.txt`, `daily-email-desktop.png`, and `daily-email-mobile.png`.
These contain fixture data only. The example email deliberately uses threshold
zero to show multiple ordered entries; production still defaults to 60.

## Assumptions and unverified behavior

The controlled skill vocabulary intentionally omits unknown technologies and
retains existing ambiguous-token conventions. The latest successful résumé by
upload time/ID supplies suggestions; saved profile skills remain authoritative.
The development candidate is still the lowest-ID profile; authentication and
multi-user settings are unchanged. Canonical creation uses cooperating ingestion
locks. Scheduling assumes an always-running host, available database, and an
explicitly understood host timezone. A Resend account, key, and verified sender
must be configured externally.

No real Resend request/delivery, verified-domain setup, bounce/webhook behavior,
installed cron execution, or OS scheduler restart behavior was exercised. Live
GitHub/job-page network refresh was not repeated for this task: workflow tests
used real parsers/normalization/reconciliation with retained fixtures. Browser
email rendering was reviewed; Gmail/Outlook inbox rendering, physical devices,
Safari/Firefox, and screen-reader announcements were not tested. Provider
acceptance is not confirmed inbox delivery.

## Files changed or added

This inventory includes the earlier uncommitted daily-refresh draft reused and
replaced by this implementation.

| File | Change |
| --- | --- |
| `.env.example` | Email-only configuration placeholders. |
| `README.md` | Architecture, profile suggestions, email setup and verification. |
| `docs/database.md` | Suggestion reads and upgraded notification ledger. |
| `docs/frontend.md` | Bounded controls, suggestion state and architecture. |
| `docs/daily-refresh.md` | Full email operations, config, discovery, retry, cron and limitations. |
| `docs/profile-and-email-report.md` | This implementation/verification inventory. |
| `frontend/src/api/resumes.ts` | Suggestion API request. |
| `frontend/src/components/Icon.tsx` | Shared plus icon. |
| `frontend/src/components/ProfileDrawer.tsx` | Latest résumé suggestion loading, cancellation and retry. |
| `frontend/src/components/ProfileForm.tsx` | Bounded controls and draft-only suggestions. |
| `frontend/src/components/ResumeUpload.tsx` | Notify parent of latest successful résumé metadata. |
| `frontend/src/components/SkillTag.tsx` | Accessible suggestion-add chip. |
| `frontend/src/components/TagInput.tsx` | Subordinate suggestion content slot. |
| `frontend/src/components/SkillSuggestions.tsx` | Filtered suggestions, Add all and status/retry. |
| `frontend/src/components/TermSelector.tsx` | Chips and predefined add select. |
| `frontend/src/lib/form.ts` | Open-ended pending fields separated from selected terms. |
| `frontend/src/lib/form.test.ts` | Updated pending-field fixture. |
| `frontend/src/lib/profile-options.ts` | Years, terms, alias-aware skill comparison. |
| `frontend/src/lib/profile-options.test.ts` | Bounded options, duplicates and alias/collision filtering. |
| `frontend/src/styles.css` | Select/suggestion presentation. |
| `frontend/src/types/profile.ts` | Pending tag and suggestion types. |
| `internshipfeed/api.py` | Candidate-scoped skill suggestion endpoint. |
| `internshipfeed/models.py` | Typed skill suggestion response. |
| `internshipfeed/resume_skills.py` | Deterministic technical phrase extraction. |
| `internshipfeed/enrich.py` | Shared React Native vocabulary and collision protection. |
| `internshipfeed/db.py` | Email ledger writes and merge-aware unnotified selection. |
| `internshipfeed/ingestion.py` | Exact current-run canonical discovery IDs. |
| `internshipfeed/daily_refresh.py` | One-shot email orchestration/CLI/dry run. |
| `internshipfeed/notifications.py` | Escaped HTML/text digest and official Resend SDK wrapper. |
| `internshipfeed/migrations/009_job_notifications.sql` | Preserved prior ledger creation migration. |
| `internshipfeed/migrations/010_email_notifications.sql` | Non-destructive provider-column/channel upgrade. |
| `pyproject.toml` | Resend and Python 3.9 HTTP-library compatibility dependencies. |
| `scripts/check_profile_ui.py` | Real bounded-input/PDF/suggestion/save/replacement checks. |
| `scripts/check_daily_email.py` | Isolated fixture dry-run/API-order/render verification. |
| `scripts/daily_refresh.cron.example` | External 09:00 host-cron template. |
| `tests/test_database.py` | Scoped suggestions and real ingestion/ranking/ledger/migration coverage. |
| `tests/test_daily_refresh.py` | SDK mocked, formatting/config/CLI/failure tests. |
| `tests/test_resume_skills.py` | Vocabulary, aliases, collisions, ordering and generic-prose tests. |

## Review points

1. **Term/year state:** `profile-options.ts`, `TermSelector.tsx`, `ProfileForm.tsx`,
   and `form.ts`; inspect null serialization, duplicates, and legacy preservation.
2. **Résumé phrase matching:** `resume_skills.py` plus the small `enrich.py` change;
   inspect controlled taxonomy, ambiguous-token rules and longest-span precedence.
3. **Suggestions → draft:** `SkillSuggestions.tsx`, `ProfileDrawer.tsx`, and
   `ProfileForm.tsx`; inspect alias filtering, stale-request cancellation and Save.
4. **Daily orchestration:** `daily_refresh.py`; inspect partial/full failure,
   retained batch retry, session locking, and transaction boundaries.
5. **New discoveries:** `ingestion.py`; inspect baseline identities and surviving
   roots under the existing sync lock, before run report persistence.
6. **Ranker reuse:** the calls to `build_behavior_profile()` and
   `rank_recommendations()` in `daily_refresh.py`; inspect eligibility and final-score
   threshold against the existing Recommended API.
7. **Resend wrapper:** `notifications.py`; inspect escaped bodies, safe links,
   SDK payload, redacted failures and stable provider idempotency key.
8. **Notification deduplication:** `db.unnotified_job_ids()`,
   `db.record_job_notifications()`, migrations 009/010, and merge/migration tests;
   inspect candidate/job/email uniqueness and provider-acceptance sequencing.
