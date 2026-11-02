# Candidate profile frontend

The profile experience is implemented in React + TypeScript, built with Vite.
It coexists with the existing vanilla jobs feed. There is no router package,
global state library, UI framework, or duplicated recommendation logic.

## Run

With Node.js 22.12+ and the existing Python/PostgreSQL setup:

```sh
npm --prefix frontend ci
npm --prefix frontend run build
.venv/bin/python -m uvicorn internshipfeed.api:app --host 127.0.0.1 --port 8000
```

Visit `/profile` for React and `/feed` or `/` for the existing feed. Both navigation
bars link Feed and Profile. FastAPI serves the compiled HTML at `/profile` and
`/profile/`, with only compiled assets under `/profile/assets`. It returns HTTP
503 if the profile build is absent. Existing APIs and `/web` assets are unchanged.
`frontend/dist` and `node_modules` are ignored; `package-lock.json` is committed.

For live development, keep FastAPI running and use another terminal:

```sh
npm --prefix frontend run dev
```

Open `http://127.0.0.1:5173/profile/`. Vite proxies `/api`, `/feed`, and `/web`
to FastAPI. If the backend uses another port:

```sh
BACKEND_URL=http://127.0.0.1:8004 npm --prefix frontend run dev
```

The proxy preserves the browser's Host/Origin pair so the existing local-only
mutation checks work without changing CORS or backend access rules. All production
requests are same-origin. No frontend environment variable contains credentials.

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

## Files

| File | Purpose |
| --- | --- |
| `frontend/package.json` | React/build/font dependencies and dev/build/test scripts. |
| `frontend/package-lock.json` | Reproducible dependency versions. |
| `frontend/tsconfig.json` | Strict TypeScript and JSX configuration. |
| `frontend/vite.config.ts` | React build base and same-origin development proxies. |
| `frontend/index.html` | Profile HTML entry. |
| `frontend/src/main.tsx` | React bootstrap, local fonts, and stylesheet. |
| `frontend/src/App.tsx` | Compact navigation and profile page shell. |
| `frontend/src/types/profile.ts` | Profile, draft, pending-tag, and résumé metadata types. |
| `frontend/src/api/http.ts` | Central JSON requests and typed HTTP errors. |
| `frontend/src/api/profile.ts` | GET/PUT profile requests. |
| `frontend/src/api/resumes.ts` | Multipart PDF upload, returning metadata only. |
| `frontend/src/lib/form.ts` | Tag operations, editable payload construction, and input validation. |
| `frontend/src/lib/form.test.ts` | Built-in Node tests for the pure form/file helpers. |
| `frontend/src/components/TopNav.tsx` | Feed/Profile navigation. |
| `frontend/src/components/TagInput.tsx` | Labelled chip editing and keyboard behavior. |
| `frontend/src/components/ProfileForm.tsx` | Draft, dirty state, full PUT, and save/error states. |
| `frontend/src/components/ResumeUpload.tsx` | Session upload state and safe error messages. |
| `frontend/src/pages/ProfilePage.tsx` | Loading/retry, composition, and request cancellation. |
| `frontend/src/styles.css` | Design tokens, layout, controls, focus, and mobile styles. |
| `internshipfeed/api.py` | `/feed` alias and compiled profile HTML/assets serving only. |
| `index.html` | Adds navigation to the existing feed. |
| `web/styles.css` | Styles that small feed navigation bar. |
| `.gitignore` | Excludes Node modules and generated build artifacts. |
| `scripts/check_profile_ui.py` | Actual profile/browser checks plus bounded error fixtures. |
| `README.md`, `docs/database.md`, `docs/frontend.md` | Setup and implemented architecture. |

## Verification

```sh
npm --prefix frontend test
npm --prefix frontend run build
.venv/bin/python scripts/test_with_existing_postgres.py
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
