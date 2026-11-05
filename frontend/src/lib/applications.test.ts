// Verify application API contracts, supported status labels, and Applied-only controls.
import assert from 'node:assert/strict';
import test, { mock } from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { updateApplication } from '../api/applications';
import { getAppliedJobs } from '../api/recommendations';
import { ApplicationStatusControl } from '../components/ApplicationStatusControl';
import { RecommendationCard } from '../components/RecommendationCard';
import { emptyInteraction } from './workflow';
import { APPLICATION_STATUSES } from './applications';
import type { CandidateJob, JobApplication, JobView } from '../types/jobs';

const application: JobApplication = { id: 1, candidate_profile_id: 1, canonical_job_id: 1, status: 'interview',
  applied_at: '2026-11-01T12:00:00Z', status_updated_at: '2026-11-04T12:00:00Z', created_at: '2026-11-01T12:00:00Z', updated_at: '2026-11-04T12:00:00Z' };
const job: CandidateJob = { job: { id: 1, company: 'Acme', title: 'Software Intern', location: 'Toronto', application_url: null, posted_at: null, status: 'open' },
  application, interaction_state: { ...emptyInteraction(1), applied: true }, term: null,
  keywords: { languages: [], frameworks: [], tools_and_platforms: [], concepts: [], requirements: [] },
  score: 0, role_score: 0, skills_score: 0, location_score: 0, term_score: 0, matched_skills: [], reasons: [] };

test('application updates use PATCH and preserve the server application state', async () => {
  const fetch = mock.method(globalThis, 'fetch', async () => new Response(JSON.stringify(application)));
  try {
    assert.deepEqual(await updateApplication(1, 'interview'), application);
    const [path, options] = fetch.mock.calls[0].arguments as unknown as [string, RequestInit];
    assert.equal(path, '/api/jobs/1/application');
    assert.equal(options.method, 'PATCH');
    assert.deepEqual(JSON.parse(options.body as string), { status: 'interview' });
  } finally { fetch.mock.restore(); }
});
test('Applied status filters are sent to the backend', async () => {
  const fetch = mock.method(globalThis, 'fetch', async () => new Response('[]'));
  try {
    await getAppliedJobs(undefined, 'rejected'); await getAppliedJobs();
    assert.deepEqual(fetch.mock.calls.map(call => call.arguments[0]), ['/api/applied?limit=50&offset=0&status=rejected', '/api/applied?limit=50&offset=0']);
  } finally { fetch.mock.restore(); }
});
test('status controls are limited to Applied and use the original application date', () => {
  assert.deepEqual(APPLICATION_STATUSES.map(([, label]) => label), ['Applied', 'Interview', 'Rejected', 'Ghosted', 'Offered']);
  for (const view of ['recommended', 'saved', 'dismissed', 'applied'] satisfies JobView[]) {
    const html = renderToStaticMarkup(createElement(RecommendationCard, { view, recommendation: job, interactionState: job.interaction_state,
      applicationControl: createElement(ApplicationStatusControl, { application, disabled: false, onUpdated() {} }),
      onApply() {}, onSave() {}, onApplied() {}, onDismiss() {} }));
    if (view === 'applied') {
      assert.match(html, /application-status-1/);
      assert.match(html, /dateTime="2026-11-01T12:00:00Z"/);
      assert.doesNotMatch(html, /dateTime="2026-11-04/);
    } else assert.doesNotMatch(html, /application-status-1/);
  }
});
