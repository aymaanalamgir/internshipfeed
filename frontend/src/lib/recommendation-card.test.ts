// Verify score presentation and recommendation API ordering contracts.
import assert from 'node:assert/strict';
import test, { mock } from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { getRecommendations } from '../api/recommendations';
import { RecommendationCard } from '../components/RecommendationCard';
import { emptyInteraction } from './workflow';
import type { JobView, RankedRecommendation } from '../types/jobs';

const ranked: RankedRecommendation = {
  job: { id: 1, company: 'Acme', title: 'Backend Intern', location: 'Toronto', application_url: null, posted_at: null, status: 'open' },
  score: 80, profile_score: 80, behavior_score: 96, final_score: 84,
  role_score: 40, skills_score: 30, location_score: 0, term_score: 10, matched_skills: [], reasons: [],
  behavior_reasons: ["Similar to roles you've shown interest in."], interaction_state: emptyInteraction(1),
  term: 'Summer 2027', keywords: { languages: ['Python'], frameworks: [], tools_and_platforms: [], concepts: [], requirements: [] },
};

function render(view: JobView) {
  return renderToStaticMarkup(createElement(RecommendationCard, {
    view, recommendation: ranked, interactionState: ranked.interaction_state,
    onSave() {}, onDismiss() {}, onApply() {}, onApplied() {},
  }));
}

test('Recommended displays the server final score as its primary score', () => {
  assert.match(render('recommended'), /class="match-score">84<small>/);
});
test('all scored collections display the same genuine upstream posting date', () => {
  for (const view of ['recommended', 'saved', 'applied', 'dismissed'] as const) {
    const html = renderToStaticMarkup(createElement(RecommendationCard, {
      view, recommendation: { ...ranked, job: { ...ranked.job, posted_at: '2026-11-03' } }, interactionState: ranked.interaction_state,
      onSave() {}, onDismiss() {}, onApply() {}, onApplied() {},
    }));
    assert.match(html, /Posted <time dateTime="2026-11-03"/);
  }
});
test('Saved and Applied retain the independent profile fit presentation', () => {
  for (const view of ['saved', 'applied'] as const) assert.match(render(view), /class="match-score">80<small>/);
});
test('recommendation API preserves server ordering and separate scoring signals', async () => {
  const results = [{ ...ranked, job: { ...ranked.job, id: 2 }, score: 70, profile_score: 70, final_score: 77 },
    { ...ranked, final_score: 72 }];
  const fetch = mock.method(globalThis, 'fetch', async () => new Response(JSON.stringify(results), { status: 200 }));
  try { assert.deepEqual(await getRecommendations(), results); }
  finally { fetch.mock.restore(); }
});
