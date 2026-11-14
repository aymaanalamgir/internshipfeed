// Verify the Region > province/state/country > city cascade.
import assert from 'node:assert/strict';
import test from 'node:test';
import { areaPlaceholder, areasFor, chooseArea, chooseRegion, citiesFor, emptyFilters } from './listings';
import type { LocationRegion } from '../types/jobs';

const regions: LocationRegion[] = [
  { name: 'Canada', areas: [{ name: 'Remote (Canada)', cities: [] }, { name: 'Ontario', cities: ['Ottawa, ON', 'Toronto, ON'] }] },
  { name: 'United States', areas: [{ name: 'Remote (US)', cities: [] }, { name: 'Texas', cities: ['Austin, TX'] }] },
  { name: 'Remote', areas: [{ name: 'Remote (Anywhere)', cities: [] }, { name: 'Remote (Canada)', cities: [] }, { name: 'Remote (US)', cities: [] }] },
];

test('provinces and remote come before cities, which need an area', () => {
  assert.deepEqual(areasFor(regions, 'Canada').map(area => area.name), ['Remote (Canada)', 'Ontario']);
  assert.deepEqual(citiesFor(regions, 'Canada', ''), []);
  assert.deepEqual(citiesFor(regions, 'Canada', 'Ontario'), ['Ottawa, ON', 'Toronto, ON']);
  assert.deepEqual(citiesFor(regions, 'Canada', 'Remote (Canada)'), []);
  assert.deepEqual(areasFor(regions, 'Remote').map(area => area.name), ['Remote (Anywhere)', 'Remote (Canada)', 'Remote (US)']);
  assert.deepEqual(areasFor(regions, ''), []);
});

test('placeholders name the middle level per region', () => {
  assert.equal(areaPlaceholder('Canada'), 'All provinces');
  assert.equal(areaPlaceholder('United States'), 'All states');
  assert.equal(areaPlaceholder('International'), 'All countries');
  assert.equal(areaPlaceholder('Remote'), 'All remote');
});

test('changing a level clears the levels beneath it', () => {
  const selected = { ...emptyFilters(), region: 'Canada', area: 'Ontario', location: 'Toronto, ON', term: 'Fall 2026' };
  assert.deepEqual(chooseRegion(selected, 'United States'), { ...selected, region: 'United States', area: '', location: '' });
  assert.deepEqual(chooseArea(selected, 'Remote (Canada)'), { ...selected, area: 'Remote (Canada)', location: '' });
});
