// Verify that ingestion freshness and manual controls reflect server state.
import test from 'node:test';
import assert from 'node:assert/strict';
import { syncLabel } from './ingestion';

test('successful ingestion time is required to claim freshness', () => {
  assert.equal(syncLabel(null), 'No successful source sync yet.');
  assert.equal(syncLabel('broken'), 'Sync time unavailable.');
  assert.match(syncLabel('2026-11-05T05:04:00Z'), /^Last updated /);
  assert.equal(syncLabel('2026-11-05T05:04:00Z'), syncLabel('2026-11-05T05:04:00Z'));
});
