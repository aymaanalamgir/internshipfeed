// Verify confirmed state, reversible collection membership, and interaction API contracts.
import assert from 'node:assert/strict';
import test, { mock } from 'node:test';
import { recordInteraction } from '../api/interactions';
import { getAppliedJobs, getDismissedJobs, getRecommendations, getSavedJobs } from '../api/recommendations';
import { applicationUrl, confirmInteraction, emptyInteraction, indexInteractions, visibleJobs } from './workflow';
import type { InteractionReceipt, InteractionState, InteractionType, Recommendation } from '../types/jobs';

function receipt(type: InteractionType, state: Partial<InteractionState> = {}, jobId = 1, version = 1): InteractionReceipt {
  return { id: version, candidate_profile_id: 1, canonical_job_id: jobId, interaction_type: type, created_at: '2026-11-02T12:00:00Z',
    interaction_state: { ...emptyInteraction(jobId), last_interaction_at: '2026-11-02T12:00:00Z', last_event_id: version, ...state } };
}
const recommendation: Recommendation = {
  job: { id: 1, company: 'Acme', title: 'Backend Intern', location: 'Toronto', application_url: 'https://example.com/job/1', posted_at: null, status: 'open' },
  score: 80, role_score: 40, skills_score: 30, location_score: 0, term_score: 10, matched_skills: ['Python'], reasons: ['Role matches Backend Engineering'],
};

test('a confirmed save installs the SQL-derived snapshot', () => {
  const response = receipt('save', { saved: true, saved_at: '2026-11-02T12:00:00Z' });
  const states = confirmInteraction({}, 1, response);
  assert.deepEqual(states[1], response.interaction_state);
  assert.deepEqual(visibleJobs('recommended', [recommendation], states), []);
  assert.deepEqual(visibleJobs('saved', [recommendation], states), [recommendation]);
});
test('click never marks the candidate applied', () => {
  const response = receipt('click');
  const states = confirmInteraction({}, 1, response);
  assert.deepEqual(states[1], response.interaction_state);
  assert.equal(states[1].applied, false);
  assert.deepEqual(visibleJobs('recommended', [recommendation], states), [recommendation]);
});
test('applied moves from discovery to Applied', () => {
  const states = confirmInteraction({}, 1, receipt('applied', { applied: true, applied_at: '2026-11-02T12:00:00Z' }));
  assert.equal(states[1].applied, true);
  assert.deepEqual(visibleJobs('recommended', [recommendation], states), []);
  assert.deepEqual(visibleJobs('applied', [recommendation], states), [recommendation]);
});
test('dismiss removes the card only after confirmed state is provided', () => {
  assert.equal(visibleJobs('recommended', [recommendation], {}).length, 1);
  const states = confirmInteraction({}, 1, receipt('dismiss', { dismissed: true }));
  assert.deepEqual(visibleJobs('recommended', [recommendation], states), []);
  assert.equal(recommendation.score, 80);
});
test('Saved + Applied stays in both history views without score changes', () => {
  const states = confirmInteraction({}, 1, receipt('applied', { saved: true, applied: true }));
  assert.deepEqual(visibleJobs('recommended', [recommendation], states), []);
  assert.deepEqual(visibleJobs('saved', [recommendation], states), [recommendation]);
  assert.deepEqual(visibleJobs('applied', [recommendation], states), [recommendation]);
  assert.equal(recommendation.score, 80);
});
test('dismiss has precedence in Saved, while Applied retains history', () => {
  const states = confirmInteraction({}, 1, receipt('dismiss', { saved: true, applied: true, dismissed: true }));
  assert.deepEqual(visibleJobs('recommended', [recommendation], states), []);
  assert.deepEqual(visibleJobs('saved', [recommendation], states), []);
  assert.deepEqual(visibleJobs('applied', [recommendation], states), [recommendation]);
});
test('merged IDs install the server snapshot under both displayed and current IDs', () => {
  const initial = { 1: { ...emptyInteraction(1), saved: true } };
  const result = confirmInteraction(initial, 1, receipt('click', { saved: true, applied: true }, 2));
  assert.equal(result[1].saved && result[1].applied, true);
  assert.equal(result[2].saved && result[2].applied, true);
  assert.equal(initial[1].applied, false);
});
test('late responses cannot replace a newer event-cursor snapshot', () => {
  const initial = { 1: { ...emptyInteraction(1), applied: true, last_event_id: 9 } };
  assert.deepEqual(confirmInteraction(initial, 1, receipt('save', { saved: true }))[1], initial[1]);
});
test('confirmed inverses remove flags and prevent late positive receipts restoring them', () => {
  const saved = confirmInteraction({}, 1, receipt('save', { saved: true }, 1, 1));
  const removed = confirmInteraction(saved, 1, receipt('unsave', { saved: false }, 1, 2));
  assert.equal(removed[1].saved, false);
  assert.deepEqual(visibleJobs('saved', [recommendation], removed), []);
  assert.equal(confirmInteraction(removed, 1, receipt('save', { saved: true }, 1, 1))[1].saved, false);
  const applied = confirmInteraction(removed, 1, receipt('applied', { applied: true }, 1, 3));
  const unmarked = confirmInteraction(applied, 1, receipt('unapplied', { applied: false }, 1, 4));
  assert.deepEqual(visibleJobs('applied', [recommendation], unmarked), []);
  assert.deepEqual(visibleJobs('recommended', [recommendation], unmarked), [recommendation]);
  assert.equal(confirmInteraction(unmarked, 1, receipt('applied', { applied: true }, 1, 3))[1].applied, false);
});
test('inactive saved/applied jobs remain in history but not discovery', () => {
  const jobs = [{ ...recommendation, job: { ...recommendation.job, status: 'closed' as const } }];
  const states = indexInteractions([{ ...emptyInteraction(1), saved: true, applied: true }]);
  assert.deepEqual(visibleJobs('recommended', jobs, states), []);
  assert.deepEqual(visibleJobs('saved', jobs, states), jobs);
  assert.deepEqual(visibleJobs('applied', jobs, states), jobs);
});
test('persisted dismiss flags restore correctly', () => {
  const states = indexInteractions([{ ...emptyInteraction(1), dismissed: true, saved: true }]);
  assert.deepEqual(visibleJobs('recommended', [recommendation], states), []);
  assert.deepEqual(visibleJobs('saved', [recommendation], states), []);
});
test('Apply permits only absolute credential-free HTTP URLs', () => {
  assert.equal(applicationUrl('https://example.com/job/1'), 'https://example.com/job/1');
  for (const value of [null, '', '/job/1', 'javascript:alert(1)', 'data:text/html,test', 'https://user:password@example.com/']) assert.equal(applicationUrl(value), null);
});
test('interaction API posts only the action and returns confirmation', async () => {
  const fetch = mock.method(globalThis, 'fetch', async () => new Response(JSON.stringify(receipt('save', { saved: true })), { status: 201 }));
  try {
    assert.equal((await recordInteraction(1, 'save')).interaction_type, 'save');
    const [path, options] = fetch.mock.calls[0].arguments as unknown as [string, RequestInit];
    assert.equal(path, '/api/jobs/1/interactions');
    assert.equal(options.method, 'POST');
    assert.deepEqual(JSON.parse(options.body as string), { type: 'save' });
  } finally { fetch.mock.restore(); }
});
test('each collection uses its backend product-state endpoint', async () => {
  const fetch = mock.method(globalThis, 'fetch', async () => new Response('[]', { status: 200 }));
  try {
    await getRecommendations(); await getSavedJobs(); await getAppliedJobs(); await getDismissedJobs();
    assert.deepEqual(fetch.mock.calls.map(call => call.arguments[0]), ['/api/recommendations?limit=30&offset=0', '/api/saved?limit=50&offset=0', '/api/applied?limit=50&offset=0', '/api/dismissed?limit=50&offset=0']);
  } finally { fetch.mock.restore(); }
});

test('Dismissed retains history; restoring preserves Save/Applied and rejects stale dismissal receipts', () => {
  const dismissed = confirmInteraction({}, 1, receipt('dismiss', { saved: true, applied: true, dismissed: true }, 1, 1));
  const inactive = [{ ...recommendation, job: { ...recommendation.job, status: 'closed' as const } }];
  assert.deepEqual(visibleJobs('dismissed', inactive, dismissed), inactive);
  const restored = confirmInteraction(dismissed, 1, receipt('undismiss', { saved: true, applied: true, dismissed: false }, 1, 2));
  assert.deepEqual(visibleJobs('dismissed', [recommendation], restored), []);
  assert.deepEqual(visibleJobs('saved', [recommendation], restored), [recommendation]);
  assert.deepEqual(visibleJobs('applied', [recommendation], restored), [recommendation]);
  assert.deepEqual(visibleJobs('recommended', [recommendation], restored), []);
  assert.equal(confirmInteraction(restored, 1, receipt('dismiss', { dismissed: true }, 1, 1))[1].dismissed, false);
  const discovery = confirmInteraction({}, 1, receipt('undismiss', { dismissed: false }));
  assert.deepEqual(visibleJobs('recommended', [recommendation], discovery), [recommendation]);
});
