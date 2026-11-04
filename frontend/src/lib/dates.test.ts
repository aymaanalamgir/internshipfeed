// Verify calendar-day formatting, missing-date behavior, and all scored card variants.
import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { postedDateLabel } from './dates';
import { PostedDate } from '../components/PostedDate';

test('upstream date displays a stable calendar day and exact machine-readable date', () => {
  assert.equal(postedDateLabel('2026-11-03'), 'Nov 3, 2026');
  const html = renderToStaticMarkup(createElement(PostedDate, { date: '2026-11-03' }));
  assert.match(html, /Posted <time dateTime="2026-11-03" title="2026-11-03">Nov 3, 2026/);
});

test('missing and invalid dates are never fabricated or rolled into a different day', () => {
  for (const date of [null, '', '2d', 'Nov 3', '2026-02-30', '2026-13-01', 'invalid']) {
    assert.equal(postedDateLabel(date), null);
    assert.equal(renderToStaticMarkup(createElement(PostedDate, { date })), '');
  }
  assert.match(renderToStaticMarkup(createElement(PostedDate, { date: null, showUnknown: true })), /Posting date unavailable/);
});
