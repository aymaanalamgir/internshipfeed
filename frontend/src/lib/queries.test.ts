// Verify real query definitions retain data through navigation, refresh, failure, and invalidation.
import assert from 'node:assert/strict';
import test, { mock } from 'node:test';
import { QueryObserver } from '@tanstack/react-query';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { LoadingState } from '../components/LoadingState';
import { collectionQuery, createQueryClient, invalidateJobs, queryKeys, sourcesQuery } from './queries';
import { emptyInteraction } from './workflow';
import type { CandidateJob } from '../types/jobs';

const cached: CandidateJob[] = [{
  job: { id: 420, company: 'Acme', title: 'Intern', location: 'Toronto', application_url: null, posted_at: null, status: 'open' },
  interaction_state: emptyInteraction(420), term: null,
  keywords: { languages: [], frameworks: [], tools_and_platforms: [], concepts: [], requirements: [] },
  score: 0, role_score: 0, skills_score: 0, location_score: 0, term_score: 0, matched_skills: [], reasons: [],
}];

test('initial loading becomes cached data and revisiting fresh tabs avoids another request', async () => {
  const client = createQueryClient();
  const calls: string[] = [];
  let release!: (value: Response) => void;
  const fetch = mock.method(globalThis, 'fetch', (path: string) => {
    calls.push(path);
    return new Promise<Response>(resolve => { release = resolve; });
  });
  const observer = new QueryObserver(client, collectionQuery('recommended'));
  const unsubscribe = observer.subscribe(() => {});
  try {
    assert.equal(observer.getCurrentResult().isPending, true);
    assert.equal(observer.getCurrentResult().data, undefined);
    release(new Response(JSON.stringify(cached)));
    await observer.refetch();
    assert.equal(observer.getCurrentResult().isPending, false);
    unsubscribe();
    await client.fetchQuery({ ...collectionQuery('saved'), queryFn: async () => [] });
    const revisited = new QueryObserver(client, collectionQuery('recommended'));
    assert.deepEqual(revisited.getCurrentResult().data, cached);
    await client.fetchQuery(collectionQuery('recommended'));
    assert.deepEqual(calls, ['/api/recommendations']);
    assert.match(renderToStaticMarkup(createElement(LoadingState)), /role="status".*class="spinner"/);
  } finally { unsubscribe(); fetch.mock.restore(); client.clear(); }
});

test('background refresh and failure preserve previously loaded collection data', async () => {
  const client = createQueryClient();
  const options = { ...collectionQuery('saved'), retry: false };
  client.setQueryData(options.queryKey, cached);
  let reject!: (error: Error) => void;
  const fetch = mock.method(globalThis, 'fetch', () => new Promise<Response>((_, fail) => { reject = fail; }));
  const observer = new QueryObserver(client, options);
  const unsubscribe = observer.subscribe(() => {});
  try {
    const refreshing = observer.refetch();
    assert.equal(observer.getCurrentResult().isFetching, true);
    assert.deepEqual(observer.getCurrentResult().data, cached);
    reject(new Error('Offline'));
    await refreshing;
    assert.equal(observer.getCurrentResult().isError, true);
    assert.deepEqual(observer.getCurrentResult().data, cached);
  } finally { unsubscribe(); fetch.mock.restore(); client.clear(); }
});

test('job invalidation covers every collection, status filter and details while retaining their cache', async () => {
  const client = createQueryClient();
  const keys = [queryKeys.collection('recommended'), queryKeys.collection('saved'), queryKeys.collection('dismissed'),
    queryKeys.collection('applied'), queryKeys.collection('applied', 'interview'), queryKeys.listings, queryKeys.details(420)];
  try {
    for (const key of keys) client.setQueryData(key, []);
    client.setQueryData(sourcesQuery().queryKey, []);
    await invalidateJobs(client);
    for (const key of keys) {
      assert.equal(client.getQueryState(key)?.isInvalidated, true);
      assert.deepEqual(client.getQueryData(key), []);
    }
    assert.equal(client.getQueryState(queryKeys.sources)?.isInvalidated, false);
  } finally { client.clear(); }
});
