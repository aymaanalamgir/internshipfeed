// Verify that recommendation explanations stay readable, evidence-based, and free of ranking internals.
import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { MatchExplanationContent, MatchSummary } from '../components/MatchSummary';
import { RecommendationCard } from '../components/RecommendationCard';
import { explainRecommendation } from './recommendation-explanation';
import { emptyInteraction } from './workflow';
import type { RankedRecommendation } from '../types/jobs';
import type { ProfileValues } from '../types/profile';

const profile: ProfileValues = {
  target_roles: ['Backend Engineering'], skills: ['Python', 'PostgreSQL'],
  preferred_locations: ['Toronto'], preferred_terms: ['Summer 2027'], graduation_year: 2027,
};
const emptyProfile: ProfileValues = { target_roles: [], skills: [], preferred_locations: [], preferred_terms: [], graduation_year: 2027 };
const recommendation: RankedRecommendation = {
  job: { id: 1, company: 'Acme', title: 'Backend Intern', location: 'Toronto, ON', application_url: null, posted_at: null, status: 'open' },
  score: 92, profile_score: 92, behavior_score: 50, final_score: 82,
  role_score: 40, skills_score: 22, location_score: 20, term_score: 10,
  matched_skills: ['Python', 'PostgreSQL'], reasons: [
    'Role matches Backend Engineering via "Backend Intern" (phrase or role-family alias)',
    'Matched skills: Python, PostgreSQL (2 of 3 detected job skills)',
    'Preferred location matches Toronto via "Toronto, ON" (city)',
    'Preferred term matches Summer 2027 via "Summer 2027" (season and year)',
  ],
  behavior_reasons: [], interaction_state: emptyInteraction(1), term: 'Summer 2027',
  keywords: { languages: ['Python'], frameworks: [], tools_and_platforms: ['PostgreSQL', 'Docker'], concepts: [], requirements: [] },
};
const unmatched: RankedRecommendation = { ...recommendation, score: 0, profile_score: 0, final_score: 13,
  role_score: 0, skills_score: 0, location_score: 0, term_score: 0, matched_skills: [], reasons: [] };
const forbidden = /profile fit|behavior score|preference (?:fit|baseline)|component weight|75%|25%|starts at 50|\b\d+\s*\/\s*(?:10|20|30|40|100)\b|role-family alias|detected job skills|role tokens/i;

function expanded(item = recommendation, candidate: ProfileValues | undefined = profile, personalized = true) {
  return renderToStaticMarkup(createElement(MatchExplanationContent, {
    explanation: explainRecommendation(item, personalized, candidate), onOpenProfile() {},
  }));
}

test('the card retains the existing final recommendation score and one compact hint', () => {
  const html = renderToStaticMarkup(createElement(RecommendationCard, {
    recommendation, profile, interactionState: recommendation.interaction_state, onApply() {}, onSave() {}, onDismiss() {}, onApplied() {},
  }));
  assert.match(html, /class="match-score">82<small>% match/);
  assert.match(html, /Backend Engineering · Python \+ PostgreSQL/);
  assert.equal([...html.matchAll(/class="match-hint"/g)].length, 1);
  assert.match(html, /aria-expanded="false"/);
  assert.match(html, /Why this job\?/);
  assert.doesNotMatch(html, forbidden);
});

test('expanded explanations show positive role, skills, location, and term evidence', () => {
  const html = expanded();
  for (const reason of ['Matches your target role: Backend Engineering', 'Matches your skills: Python · PostgreSQL',
    'Matches your preferred location: Toronto', 'Fits Summer 2027']) assert.ok(html.includes(reason));
  assert.doesNotMatch(html, forbidden);
  assert.doesNotMatch(html, /ranking-breakdown|score-breakdown|ranking-formula|<dl/);
});

test('limited matches explain only configured preferences without zero-filled components', () => {
  const html = expanded(unmatched);
  assert.match(html, /Limited match/);
  assert.match(html, /Role is outside your main targets/);
  assert.match(html, /Few overlapping skills/);
  assert.doesNotMatch(html, forbidden);
  const locationOnly = expanded(unmatched, { ...emptyProfile, preferred_locations: ['Vancouver'] });
  assert.match(locationOnly, /Location isn&#x27;t one of your preferences/);
  assert.doesNotMatch(locationOnly, /Role is outside|Few overlapping skills/);
});

test('missing job metadata is not treated as evidence of a skill or location mismatch', () => {
  const html = expanded({ ...unmatched, job: { ...unmatched.job, title: 'Unknown', location: 'Not specified' },
    keywords: { languages: [], frameworks: [], tools_and_platforms: [], concepts: [], requirements: [] } });
  assert.match(html, /Limited match/);
  assert.match(html, /No strong matches in the available job details yet/);
  assert.doesNotMatch(html, /Few overlapping skills|outside your main targets|isn&#x27;t one/);
});

test('empty profiles receive guidance and an Edit profile action rather than a poor-match label', () => {
  const html = expanded(unmatched, emptyProfile);
  assert.match(html, /Personalization is still getting started\./);
  assert.match(html, /Add your target roles, skills, locations, and internship terms to improve recommendations\./);
  assert.match(html, /<button[^>]*>Edit profile<\/button>/);
  assert.doesNotMatch(html, /Limited match|Few overlapping|outside your main targets/);
  assert.doesNotMatch(html, forbidden);
  assert.equal(explainRecommendation(unmatched, true, emptyProfile).hint, 'Personalization is still getting started.');
});

test('unloaded profiles are not guessed to be empty', () => {
  const explanation = explainRecommendation(unmatched, true);
  assert.equal(explanation.emptyProfile, false);
  assert.deepEqual(explanation.gaps, []);
});

test('cold-start behavior math is omitted entirely', () => {
  const html = expanded();
  assert.doesNotMatch(html, /behavior-reasons|preferences|50|75|25/);
  assert.doesNotMatch(html, forbidden);
});

test('behavioral explanations preserve supported interest without claiming a specific action', () => {
  const item = { ...recommendation, behavior_reasons: [
    "Similar to roles you've shown interest in: Backend Engineering.",
    "Shares skills with jobs you've shown interest in: Python, PostgreSQL.",
  ] };
  const html = expanded(item);
  assert.match(html, /Similar to roles you&#x27;ve shown interest in/);
  assert.match(html, /Matches skills you&#x27;ve shown interest in: Python, PostgreSQL/);
  assert.doesNotMatch(html, /saved|applied|behavior score|preference baseline/);
  assert.doesNotMatch(expanded(item, profile, false), /behavior-reasons|shown interest/);
  assert.match(expanded({ ...unmatched, behavior_reasons: item.behavior_reasons }, emptyProfile), /Personalization is still getting started/);
  assert.match(expanded({ ...unmatched, behavior_reasons: item.behavior_reasons }, emptyProfile), /shown interest/);
});

test('unrecognized diagnostic reasons are not leaked and matched skills remain escaped', () => {
  const item = { ...recommendation, reasons: ['Role 40 / 40; profile fit 92'],
    behavior_reasons: ['Preference fit starts at 50'], matched_skills: ['<script>Python</script>'] };
  const html = expanded(item);
  assert.match(html, /Matches your target role/);
  assert.match(html, /&lt;script&gt;Python&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>|starts at 50/);
  assert.doesNotMatch(html, forbidden);
});

test('partial geography and term matches do not imply an exact city or complete term match', () => {
  const item = { ...recommendation, location_score: 8, term_score: 6, reasons: [
    'Preferred location matches Toronto, ON via "Vancouver, BC" (country overlap only)',
    'Preferred term matches Summer 2027 via "Summer" (season only; year missing)',
  ] };
  const html = expanded(item);
  assert.match(html, /In the same country as your preferred locations/);
  assert.match(html, /Matches your preferred season/);
  assert.doesNotMatch(html, /preferred location: Toronto|Fits Summer 2027/);
  assert.match(expanded({ ...item, reasons: ['Preferred location matches Ottawa, ON via "Toronto, ON" (province overlap)'] }), /same province/);
  assert.match(expanded({ ...item, reasons: ['Preferred term matches Summer 2027 via "2027" (year only; season missing)'] }), /preferred year/);
});

test('partial role overlap is described as related rather than an exact target match', () => {
  const html = expanded({ ...recommendation, role_score: 15 });
  assert.match(html, /Related to your target role: Backend Engineering/);
  assert.doesNotMatch(html, /Matches your target role/);
});

test('the explanation transformer leaves scores, evidence, and API ordering untouched', () => {
  const before = structuredClone(recommendation);
  explainRecommendation(recommendation, true, profile);
  renderToStaticMarkup(createElement(MatchSummary, { recommendation, personalized: true, profile }));
  assert.deepEqual(recommendation, before);
});
