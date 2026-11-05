// Verify browser identity bootstrap is serialized and candidate requests keep cookie credentials private.
import test, { mock } from 'node:test';
import assert from 'node:assert/strict';
import { ensureSession, requestJson } from '../api/http';
import { createQueryClient, queryKeys } from './queries';

test('session initialization retries failures and shares a single successful bootstrap', async () => {
  const fetch = mock.method(globalThis, 'fetch', async () => new Response('', { status: 503 }));
  try {
    await assert.rejects(ensureSession());
    let release!: (response: Response) => void;
    fetch.mock.mockImplementation(() => new Promise<Response>(resolve => { release = resolve; }));
    const first = ensureSession();
    const concurrent = ensureSession();
    assert.equal(first, concurrent);
    release(new Response(JSON.stringify({ anonymous: true, created: true })));
    await Promise.all([first, concurrent]);
    await ensureSession();
    assert.equal(fetch.mock.callCount(), 2);
    const options = fetch.mock.calls[1].arguments[1] as RequestInit;
    assert.equal(options.credentials, 'same-origin');
    assert.equal(options.cache, 'no-store');
  } finally { fetch.mock.restore(); }
});

test('candidate JSON requests use same-origin cookies and no shared cache', async () => {
  const fetch = mock.method(globalThis, 'fetch', async () => new Response(JSON.stringify({ skills: ['Python'] })));
  try {
    assert.deepEqual(await requestJson('/api/profile'), { skills: ['Python'] });
    const options = fetch.mock.calls[0].arguments[1] as RequestInit;
    assert.equal(options.credentials, 'same-origin');
    assert.equal(options.cache, 'no-store');
  } finally { fetch.mock.restore(); }
});

test('independent page query clients never share candidate data', () => {
  const first = createQueryClient();
  const second = createQueryClient();
  try {
    first.setQueryData(queryKeys.profile, { skills: ['Python'] });
    assert.equal(second.getQueryData(queryKeys.profile), undefined);
  } finally { first.clear(); second.clear(); }
});
