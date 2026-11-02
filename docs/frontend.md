# Candidate frontend

The profile and actionable recommendation screens use React + TypeScript and Vite.
It coexists with the existing vanilla jobs feed. There is no router package,
global state library, UI framework, or duplicated recommendation logic.

## Run

With Node.js 22.12+ and the existing Python/PostgreSQL setup:

```sh
npm --prefix frontend ci
npm --prefix frontend run build
.venv/bin/python -m uvicorn internshipfeed.api:app --host 127.0.0.1 --port 8000
```

Visit `/profile` for the profile, `/recommendations` for discovery, `/saved` for
bookmarks, `/applied` for application history, and
`/feed` or `/` for the existing feed. Navigation links all three screens. FastAPI
serves the same compiled React HTML at those four screen routes (including
trailing-slash forms), with compiled assets under `/profile/assets`. It returns HTTP
503 if the frontend build is absent. Restart FastAPI after backend route changes.
Existing profile/job APIs and `/web` assets are unchanged.
`frontend/dist` and `node_modules` are ignored; `package-lock.json` is committed.

For live development, keep FastAPI running and use another terminal:

```sh
npm --prefix frontend run dev
```

Open `http://127.0.0.1:5173/profile`, `/recommendations`, `/saved`, or `/applied`. Vite proxies `/api`, `/feed`, and `/web`
to FastAPI. If the backend uses another port:

```sh
BACKEND_URL=http://127.0.0.1:8004 npm --prefix frontend run dev
```

The proxy preserves the browser's Host/Origin pair so the existing local-only
mutation checks work without changing CORS or backend access rules. All production
requests are same-origin. No frontend environment variable contains credentials.
Vite uses a root base during development so both screen paths work, and the
existing `/profile/` asset base for production builds. App selects the screen by
pathname; no router package is required.

## Experience and state

`ProfilePage` loads `GET /api/profile` with cancellation on unmount. Loading uses a
static skeleton; failures show a concise message and retry button. `ProfileForm`
owns the editable draft, pending tag input, saved baseline, validation, and save
state. Inputs and controls are disabled during save, and errors preserve edits.

Roles, skills, locations, and terms use keyboard-friendly chips: Enter adds a
trimmed value, exact duplicates are ignored, and labelled buttons remove values.
Case and order remain intact. Save also includes pending text even without Enter.
The full five-field PUT payload excludes ID and timestamps. Empty lists and a
blank graduation year intentionally clear values. Client year validation follows
the backend's 1900–2200 range; the API remains authoritative. Success adopts the
normalized server response and shows a small Saved message. Refresh reloads the
persisted database state.

`ResumeUpload` opens a PDF-restricted file picker and validates filename, compatible
MIME, and the 5 MiB limit before sending multipart field `file`. The browser sets
the multipart boundary. The backend alone parses the PDF and enforces 10 pages.
The UI shows the selected filename, uploading state, success, or a concise error.
Selecting the same file again supports retry. Additional uploads append history;
they do not replace existing records or modify the profile.

Only returned résumé metadata is retained by React. Extracted text is never
displayed, inspected, or used to update preferences. Filename/status are session
state, since there is no résumé-history endpoint. Reloading shows that no file
has been uploaded in the current session. Arbitrary server diagnostics are not
rendered. There is no upload percentage because the current request uses fetch;
the Uploading state covers the request and backend extraction.

## Product views and actions

`JobCollectionPage` is reused by Recommended, Saved, and Applied. Each screen owns
its server-loaded collection and calls one API: `/api/recommendations`,
`/api/saved`, or `/api/applied`. Each response includes authoritative
`interaction_state`; there is no second state fetch or raw event replay. Loading
errors produce a retry state instead of a partially reconstructed list. Server
ordering is preserved and 24 cards are progressively rendered with Show more.

Recommended excludes dismissed/applied jobs. Saved includes saved nondismissed
jobs, including applied ones. Applied includes application history even if later
dismissed. The same card renders each view, with a UTC application date and Open
application link in Applied. Closed/unavailable postings remain in history with
their status shown. The compact navigation is Recommended/Saved/Applied/Profile;
the wordmark links to the existing job feed. Mobile navigation wraps into two rows.

`RecommendationCard` receives recommendation/state data and callbacks. It shows
company, title, location, optional exact posting date, and matched technologies.
The score is a rule score out of 100 rather than a probability. Its compact
disclosure expands the component values and unchanged reason strings.

Actions are ordered Apply, bookmark Save, clock Applied, then quiet × Dismiss.
Apply is a native HTTP(S) anchor with `target="_blank"` and `rel="noopener noreferrer"`.
Its callback starts click recording without awaiting or cancelling navigation.
Even an HTTP 503 cannot block opening the external URL. Missing or invalid links
render an unavailable Apply control. A click never marks the job applied.

Other actions use confirmed updates. The specific card disables utilities while
its request is pending, and success installs the SQL-derived snapshot returned
by POST. `last_event_id` rejects stale responses without relying on JavaScript's
millisecond timestamp precision. React never interprets event types to derive
Saved/Dismissed/Applied flags.
Failures retain the card/state and show a retryable inline error. Saved and
Applied actions are one-directional; no event history is deleted. A dismiss is
removed only after success and remains hidden after refresh. Applied jobs leave
Recommended but remain in Saved if bookmarked. A merge response updates both a
stale displayed ID and its current canonical ID using the server's state.
The API module owns requests; there are no fetch calls inside card JSX. See
[interactions.md](interactions.md) for the event projection and API contract.

## Design

Self-hosted IBM Plex Sans (400/500/600) supplies compact, legible type with no
external font request. Warm off-white `#f6f4ef`, paper `#fffefa`, ink `#252522`,
muted grey `#706e67`, and terracotta `#a44c30` are CSS tokens. Orange is limited
to focus, navigation accents, and the primary action. Borders are 1 px; radii
are 3–5 px. Spacing uses small 4–8 px gaps and 20–32 px section intervals.

The desktop margin note collapses away on mobile, chips wrap, and upload controls
stack naturally. Labels, field-associated year errors, live status messages,
semantic buttons, a skip link, and visible focus treatments support keyboard use.
There are no cards around each section, gradients, heavy shadows, or entrance
animations. The existing feed retains its behavior and presentation.
Recommendation cards use the same tokens with a solid near-black Apply link,
small utility icons, restrained active orange, and a 1 px border per opportunity.

## Files

| File | Purpose |
| --- | --- |
| `frontend/package.json` | React/build/font dependencies and dev/build/test scripts. |
| `frontend/package-lock.json` | Reproducible dependency versions. |
| `frontend/tsconfig.json` | Strict TypeScript and JSX configuration. |
| `frontend/vite.config.ts` | React build base and same-origin development proxies. |
| `frontend/index.html` | Profile HTML entry. |
| `frontend/src/main.tsx` | React bootstrap, local fonts, and stylesheet. |
| `frontend/src/App.tsx` | Profile/recommendation pathname selection and navigation. |
| `frontend/src/types/profile.ts` | Profile, draft, pending-tag, and résumé metadata types. |
| `frontend/src/types/jobs.ts` | Canonical jobs, recommendations, events, and effective state. |
| `frontend/src/api/http.ts` | Central JSON requests and typed HTTP errors. |
| `frontend/src/api/profile.ts` | GET/PUT profile requests. |
| `frontend/src/api/resumes.ts` | Multipart PDF upload, returning metadata only. |
| `frontend/src/api/recommendations.ts` | Read scored jobs from the matching API. |
| `frontend/src/api/interactions.ts` | Read state and append interaction events. |
| `frontend/src/lib/form.ts` | Tag operations, editable payload construction, and input validation. |
| `frontend/src/lib/form.test.ts` | Built-in Node tests for the pure form/file helpers. |
| `frontend/src/lib/workflow.ts` | Confirmed-state merging, dismiss presentation, and safe application URLs. |
| `frontend/src/lib/workflow.test.ts` | State/event, API payload, and navigation-URL tests. |
| `frontend/src/components/TopNav.tsx` | Feed/Profile navigation. |
| `frontend/src/components/TagInput.tsx` | Labelled chip editing and keyboard behavior. |
| `frontend/src/components/ProfileForm.tsx` | Draft, dirty state, full PUT, and save/error states. |
| `frontend/src/components/ResumeUpload.tsx` | Session upload state and safe error messages. |
| `frontend/src/components/RecommendationCard.tsx` | Job/score hierarchy and accessible action controls. |
| `frontend/src/pages/ProfilePage.tsx` | Loading/retry, composition, and request cancellation. |
| `frontend/src/pages/JobCollectionPage.tsx` | Reused Recommended/Saved/Applied screen, server snapshots, confirmed actions, and refresh restoration. |
| `frontend/src/styles.css` | Design tokens, layout, controls, focus, and mobile styles. |
| `internshipfeed/api.py` | API endpoints and compiled frontend HTML/assets serving. |
| `internshipfeed/db.py` | Append events and project candidate state across merged job IDs. |
| `internshipfeed/models.py` | Explicit event-request, event-response, and state-response contracts. |
| `index.html` | Adds navigation to the existing feed. |
| `web/styles.css` | Styles that small feed navigation bar. |
| `.gitignore` | Excludes Node modules and generated build artifacts. |
| `scripts/check_profile_ui.py` | Actual profile/browser checks plus bounded error fixtures. |
| `scripts/check_recommendations_ui.py` | Isolated PostgreSQL/browser workflow and regression checks. |
| `README.md`, `docs/database.md`, `docs/frontend.md` | Setup and implemented architecture. |

## Verification

```sh
npm --prefix frontend test
npm --prefix frontend run build
.venv/bin/python scripts/test_with_existing_postgres.py
.venv/bin/python scripts/check_recommendations_ui.py --browser-executable '/Applications/Brave Browser.app/Contents/MacOS/Brave Browser'
.venv/bin/python scripts/check_profile_ui.py --url http://127.0.0.1:8000 --browser-executable '/Applications/Brave Browser.app/Contents/MacOS/Brave Browser'
.venv/bin/python scripts/check_ui.py --url http://127.0.0.1:8000 --browser-executable '/Applications/Brave Browser.app/Contents/MacOS/Brave Browser'
```

Omit `--browser-executable` if Playwright's Chromium is installed. The profile
check writes an example profile through the UI, verifies refresh persistence,
and restores original editable values in `finally`. As with any profile update,
the database trigger advances `updated_at`. It uploads **one generated PDF**,
which remains in development résumé history; it does not delete existing data.
Other upload retries/failures use browser fixtures to avoid additional records.

Checks cover initial values, all tag fields, duplicate prevention, removal,
pending input on save, graduation validation, successful PDF upload, DOCX and
corrupt-PDF rejection, safe load/save/upload error states, retries, mobile overflow,
and feed navigation. Screenshots are written under ignored `data/screenshots`.
The separate existing feed check covers filters, job details, search, refresh,
mobile layout, and sync progress. No UI test starts real source ingestion.

The product workflow script initializes an isolated schema on the existing
PostgreSQL server, seeds a development profile and four fixture jobs, and starts a
temporary FastAPI process. It confirms save/click/dismiss/applied rows directly in
PostgreSQL, persistence across all three screens and repeated refreshes, combined
Saved/Applied and Saved/Dismissed precedence, application timestamps, confirmed-only
UI changes, error handling, unchanged scores, and 390/320 px layouts. A reserved fixture application URL opens
in a real new tab; its page is intercepted locally to avoid external employer
requests. The script then runs the existing profile and feed checks in that same
schema and removes its server/schema, preserving live candidate history.
