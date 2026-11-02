import assert from 'node:assert/strict';
import test, { mock } from 'node:test';
import { recordInteraction } from '../api/interactions';
import { applicationUrl, confirmInteraction, emptyInteraction, indexInteractions, visibleRecommendations } from './workflow';
import type { InteractionEvent, InteractionStates, InteractionType, Recommendation } from '../types/jobs';

function event(type: InteractionType, jobId = 1): InteractionEvent {
  return { id: 1, candidate_profile_id: 1, canonical_job_id: jobId, interaction_type: type, created_at: '2026-11-02T12:00:00Z' };
}
const recommendation: Recommendation = {
  job: { id: 1, company: 'Acme', title: 'Backend Intern', location: 'Toronto', application_url: 'https://example.com/job/1', posted_at: null, status: 'open' },
  score: 80, role_score: 40, skills_score: 30, location_score: 0, term_score: 10, matched_skills: ['Python'], reasons: ['Role matches Backend Engineering'],
};

test('a confirmed save activates only saved state', () => {
  const states = confirmInteraction({}, 1, event('save'));
  assert.deepEqual(states[1], { job_id: 1, saved: true, dismissed: false, applied: false, last_interaction_at: event('save').created_at });
});
test('click never marks the candidate applied', () => {
  assert.deepEqual(confirmInteraction({}, 1, event('click'))[1], { ...emptyInteraction(1), last_interaction_at: event('click').created_at });
});
test('applied remains visible with its confirmed state', () => {
  const states = confirmInteraction({}, 1, event('applied'));
  assert.equal(states[1].applied, true);
  assert.deepEqual(visibleRecommendations([recommendation], states), [recommendation]);
});
test('dismiss removes the card only after confirmed state is provided', () => {
  assert.equal(visibleRecommendations([recommendation], {}).length, 1);
  const states = confirmInteraction({}, 1, event('dismiss'));
  assert.deepEqual(visibleRecommendations([recommendation], states), []);
  assert.equal(recommendation.score, 80);
});
test('events never reset earlier saved/applied/dismissed flags', () => {
  let states: InteractionStates = {};
  for (const type of ['save', 'applied', 'dismiss', 'click'] as const) states = confirmInteraction(states, 1, event(type));
  assert.equal(states[1].saved && states[1].applied && states[1].dismissed, true);
});
test('merged response IDs retain known flags for displayed and canonical IDs', () => {
  const initial = { 1: { ...emptyInteraction(1), saved: true }, 2: { ...emptyInteraction(2), applied: true } };
  const result = confirmInteraction(initial, 1, event('click', 2));
  assert.equal(result[1].saved && result[1].applied, true);
  assert.equal(result[2].saved && result[2].applied, true);
  assert.equal(initial[1].applied, false);
});
test('late responses preserve the newest interaction timestamp', () => {
  const initial = { 1: { ...emptyInteraction(1), last_interaction_at: '2026-11-03T12:00:00Z' } };
  assert.equal(confirmInteraction(initial, 1, event('save'))[1].last_interaction_at, initial[1].last_interaction_at);
});
test('persisted flags restore correctly and inactive jobs are excluded', () => {
  const states = indexInteractions([{ ...emptyInteraction(1), dismissed: true, saved: true }]);
  assert.deepEqual(visibleRecommendations([recommendation], states), []);
  assert.deepEqual(visibleRecommendations([{ ...recommendation, job: { ...recommendation.job, status: 'dead' } }], {}), []);
});
test('Apply permits only absolute credential-free HTTP URLs', () => {
  assert.equal(applicationUrl('https://example.com/job/1'), 'https://example.com/job/1');
  for (const value of [null, '', '/job/1', 'javascript:alert(1)', 'data:text/html,test', 'https://user:password@example.com/']) assert.equal(applicationUrl(value), null);
});
test('interaction API posts only the action and returns confirmation', async () => {
  const fetch = mock.method(globalThis, 'fetch', async () => new Response(JSON.stringify(event('save')), { status: 201 }));
  try {
    assert.equal((await recordInteraction(1, 'save')).interaction_type, 'save');
    const [path, options] = fetch.mock.calls[0].arguments as unknown as [string, RequestInit];
    assert.equal(path, '/api/jobs/1/interactions');
    assert.equal(options.method, 'POST');
    assert.deepEqual(JSON.parse(options.body as string), { type: 'save' });
  } finally { fetch.mock.restore(); }
});
