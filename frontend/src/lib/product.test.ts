// Verify navigation, directory filtering, and shared tag/action rendering contracts.
import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { viewForPath } from './navigation';
import { emptyFilters, normalizeFilters } from './listings';
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
  assert.equal(viewForPath('/dismissed/'), 'dismissed'); assert.equal(viewForPath('/saved'), 'saved'); assert.equal(viewForPath('/applied/'), 'applied');
});
test('navigation exposes five destinations and Profile as a dialog control', () => {
  const html = renderToStaticMarkup(createElement(TopNav, { current: 'listings', profileOpen: false, onProfile() {}, onNavigate() {} }));
  assert.match(html, /href="\/listings" aria-current="page"/);
  assert.match(html, /href="\/dismissed"/);
  const destinations = [...html.matchAll(/<a[^>]+href="(\/listings|\/applied|\/saved|\/dismissed|\/)"[^>]*>([^<]+)<\/a>/g)].map(match => match[2]);
  assert.deepEqual(destinations, ['Recommended', 'All listings', 'Applied', 'Saved', 'Dismissed']);
  assert.match(html, /aria-haspopup="dialog"/);
  assert.doesNotMatch(html, /href="\/profile"/);
});
test('directory filters normalize search without changing exact metadata selections', () => {
  assert.deepEqual(normalizeFilters({ query: ' PYTHON ', location: 'Toronto', term: 'Summer 2027', source: '2', status: 'open' }),
    { query: 'python', location: 'Toronto', term: 'Summer 2027', source: '2', status: 'open' });
  assert.deepEqual(normalizeFilters(emptyFilters()), emptyFilters());
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
  assert.ok(html.indexOf('Apply') < html.indexOf('Save job') && html.indexOf('Save job') < html.indexOf('Dismiss job'));
  assert.match(html, /target="_blank" rel="noopener noreferrer"/);
  assert.doesNotMatch(html, /Mark job as applied/);
});
test('Applied actions retain history state and omit Dismiss', () => {
  const html = renderToStaticMarkup(createElement(JobActions, { job: listing.job, state: { ...emptyInteraction(1), applied: true, saved: true }, appliedView: true,
    onApply() {}, onSave() {}, onApplied() {}, onDismiss() {} }));
  assert.match(html, /Open application/); assert.match(html, /aria-pressed="true"/); assert.doesNotMatch(html, /Dismiss job/);
  assert.match(html, /aria-label="Unsave job"/);
  assert.match(html, /aria-label="Unmark job as applied"/);
  assert.doesNotMatch(html, /disabled/);
});

test('dismissed jobs expose an enabled Restore action, including in Applied', () => {
  for (const appliedView of [false, true]) {
    const html = renderToStaticMarkup(createElement(JobActions, { job: listing.job, state: { ...emptyInteraction(1), dismissed: true }, appliedView,
      onApply() {}, onSave() {}, onApplied() {}, onDismiss() {} }));
    assert.match(html, /aria-label="Restore job"/);
    assert.doesNotMatch(html, /aria-label="Dismiss job"/);
    assert.doesNotMatch(html, /disabled/);
  }
});

test('manual Applied is available in details and invalid URLs expose no Apply link', () => {
  const props = { job: listing.job, state: emptyInteraction(1), onApply() {}, onSave() {}, onApplied() {}, onDismiss() {} };
  assert.match(renderToStaticMarkup(createElement(JobActions, { ...props, showAppliedAction: true })), /Mark job as applied/);
  for (const url of [null, 'javascript:alert(1)', 'https://user:password@example.com/job']) {
    const html = renderToStaticMarkup(createElement(JobActions, { ...props, job: { ...listing.job, application_url: url } }));
    assert.doesNotMatch(html, /apply-action|>Apply/);
    assert.match(html, /Save job/);
  }
});
