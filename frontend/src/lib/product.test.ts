import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { viewForPath } from './navigation';
import { emptyFilters, filterListings } from './listings';
import { emptyInteraction } from './workflow';
import { SkillTag } from '../components/SkillTag';
import { JobActions } from '../components/JobActions';
import { TopNav } from '../components/TopNav';
import type { JobDetails } from '../types/jobs';

const listing: JobDetails = {
  job: { id: 1, company: 'Acme', title: 'Backend Intern', location: 'Toronto', application_url: 'https://example.com/job/1', posted_at: null, status: 'open' },
  term: 'Summer 2027', description: 'Build tools.', keywords: { languages: ['Python'], frameworks: ['FastAPI'], tools_and_platforms: ['SQL'], concepts: [], requirements: [] },
  sources: [{ source_id: 2, source: 'https://github.com/example/jobs', company: 'Acme', title: 'Software Intern', location: 'Toronto', term: 'Summer 2027', status: 'open' }],
};
test('home prioritizes Recommended and legacy paths retain compatible destinations', () => {
  for (const path of ['/', '/recommendations', '/recommendations/', '/profile']) assert.equal(viewForPath(path), 'recommended');
  for (const path of ['/listings', '/listings/', '/feed']) assert.equal(viewForPath(path), 'listings');
  assert.equal(viewForPath('/saved'), 'saved'); assert.equal(viewForPath('/applied/'), 'applied');
});
test('navigation exposes four destinations and Profile as a dialog control', () => {
  const html = renderToStaticMarkup(createElement(TopNav, { current: 'listings', profileOpen: false, onProfile() {} }));
  assert.match(html, /href="\/listings" aria-current="page"/);
  assert.match(html, /aria-haspopup="dialog"/);
  assert.doesNotMatch(html, /href="\/profile"/);
});
test('directory search covers company, technical skills, and source titles', () => {
  for (const query of ['acme', ' FASTAPI ', 'Python', 'Software Intern']) assert.deepEqual(filterListings([listing], { ...emptyFilters(), query }), [listing]);
  assert.deepEqual(filterListings([listing], { ...emptyFilters(), query: 'missing' }), []);
});
test('directory filters compose and do not invent unavailable metadata', () => {
  assert.deepEqual(filterListings([listing], { query: 'python', location: 'Toronto', term: 'Summer 2027', source: '2', status: 'open' }), [listing]);
  for (const [field, value] of Object.entries({ location: 'Ottawa', term: 'Winter 2027', source: '1', status: 'unknown' }))
    assert.deepEqual(filterListings([listing], { ...emptyFilters(), [field]: value }), []);
});
test('closed source observations do not satisfy active source filters', () => {
  const closed = { ...listing, sources: [{ ...listing.sources[0], status: 'closed' as const }] };
  assert.deepEqual(filterListings([closed], { ...emptyFilters(), source: '2' }), []);
});
test('directory preserves server order without ranking', () => {
  const entries = [{ ...listing, job: { ...listing.job, id: 9 } }, listing];
  assert.deepEqual(filterListings(entries, emptyFilters()), entries);
});
test('SkillTag uses one escaped label and an accessible optional remove control', () => {
  const plain = renderToStaticMarkup(createElement(SkillTag, { label: '<script>Python</script>' }));
  assert.match(plain, /&lt;script&gt;Python/); assert.doesNotMatch(plain, /<script>/);
  assert.doesNotMatch(plain, /<button/);
  const editable = renderToStaticMarkup(createElement(SkillTag, { label: 'Python', onRemove() {} }));
  assert.match(editable, /aria-label="Remove Python"/);
});
test('shared actions preserve order and native external application behavior', () => {
  const html = renderToStaticMarkup(createElement(JobActions, { job: listing.job, state: emptyInteraction(1), onApply() {}, onSave() {}, onApplied() {}, onDismiss() {} }));
  assert.ok(html.indexOf('Apply') < html.indexOf('Save job') && html.indexOf('Save job') < html.indexOf('Mark job as applied') && html.indexOf('Mark job as applied') < html.indexOf('Dismiss job'));
  assert.match(html, /target="_blank" rel="noopener noreferrer"/);
});
test('Applied actions retain history state and omit Dismiss', () => {
  const html = renderToStaticMarkup(createElement(JobActions, { job: listing.job, state: { ...emptyInteraction(1), applied: true, saved: true }, appliedView: true,
    onApply() {}, onSave() {}, onApplied() {}, onDismiss() {} }));
  assert.match(html, /Open application/); assert.match(html, /aria-pressed="true"/); assert.doesNotMatch(html, /Dismiss job/);
  assert.match(html, /aria-label="Unsave job"/);
  assert.match(html, /aria-label="Unmark job as applied"/);
  assert.doesNotMatch(html, /disabled/);
});
