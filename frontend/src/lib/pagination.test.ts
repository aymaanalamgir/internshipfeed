// Verify page fetching, isolated filter caches, cancellation, retries, and compact pagination states.
import assert from 'node:assert/strict';
import test, { mock } from 'node:test';
import { InfiniteQueryObserver } from '@tanstack/react-query';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { CollectionPagination } from '../components/CollectionPagination';
import { getListings } from '../api/jobs';
import { createQueryClient, listingsQuery, queryKeys } from './queries';
import { emptyFilters, listingItems } from './listings';
import { emptyInteraction } from './workflow';
import type { JobListPage } from '../types/jobs';

const page = (ids: number[], cursor: string | null = null): JobListPage => ({
  items: ids.map(id => ({ job: { id, company: 'Acme', title: 'Intern', location: 'Toronto', application_url: null,
    posted_at: null, status: 'open' }, term: null, skills: ['Python'], interaction_state: emptyInteraction(id) })),
  total: 100, limit: 50, has_more: cursor !== null, next_cursor: cursor,
});
const response = (data: JobListPage) => new Response(JSON.stringify(data), { status: 200 });

test('directory requests send bounded pages, normalized filters, cursor, and cancellation signal', async () => {
  const controller = new AbortController();
  const fetch = mock.method(globalThis, 'fetch', async () => response(page([1])));
  try {
    await getListings({ query: ' PYTHON ', source: '2', location: 'Toronto', term: 'Summer 2027', status: 'open' }, 'next', controller.signal);
    const url = new URL(String(fetch.mock.calls[0].arguments[0]), 'https://example.test');
    assert.deepEqual(Object.fromEntries(url.searchParams), { limit: '50', q: 'python', location: 'Toronto', term: 'Summer 2027', source_id: '2', status: 'open', cursor: 'next' });
    assert.equal((fetch.mock.calls[0].arguments[1] as RequestInit).signal, controller.signal);
  } finally { fetch.mock.restore(); }
});

test('Show more requests only the next page, retains the first, and deduplicates appended jobs', async () => {
  const client = createQueryClient();
  let release!: (result: Response) => void;
  const fetch = mock.method(globalThis, 'fetch', async (url: string) => url.includes('cursor=')
    ? new Promise<Response>(resolve => { release = resolve; }) : response(page([1, 2], 'next')));
  const observer = new InfiniteQueryObserver(client, { ...listingsQuery(), retry: false });
  const unsubscribe = observer.subscribe(() => {});
  try {
    await observer.refetch();
    const next = observer.fetchNextPage();
    assert.equal(observer.getCurrentResult().isFetchingNextPage, true);
    assert.deepEqual(listingItems(observer.getCurrentResult().data!.pages).map(item => item.job.id), [1, 2]);
    release(response(page([2, 3])));
    await next;
    assert.deepEqual(listingItems(observer.getCurrentResult().data!.pages).map(item => item.job.id), [1, 2, 3]);
    assert.equal(observer.getCurrentResult().hasNextPage, false);
    assert.deepEqual(fetch.mock.calls.map(call => call.arguments[0]), ['/api/feed?limit=50', '/api/feed?limit=50&cursor=next']);
  } finally { unsubscribe(); fetch.mock.restore(); client.clear(); }
});

test('a failed second page preserves loaded results and retries the same cursor once requested', async () => {
  const client = createQueryClient();
  let failing = true;
  const fetch = mock.method(globalThis, 'fetch', async (url: string) => url.includes('cursor=')
    ? failing ? new Response('', { status: 503 }) : response(page([2])) : response(page([1], 'retry')));
  const observer = new InfiniteQueryObserver(client, { ...listingsQuery(), retry: false });
  const unsubscribe = observer.subscribe(() => {});
  try {
    await observer.refetch();
    await observer.fetchNextPage();
    assert.equal(observer.getCurrentResult().isFetchNextPageError, true);
    assert.deepEqual(listingItems(observer.getCurrentResult().data!.pages).map(item => item.job.id), [1]);
    failing = false;
    await observer.fetchNextPage();
    assert.equal(observer.getCurrentResult().isFetchNextPageError, false);
    assert.deepEqual(listingItems(observer.getCurrentResult().data!.pages).map(item => item.job.id), [1, 2]);
    assert.equal(fetch.mock.calls[1].arguments[0], fetch.mock.calls[2].arguments[0]);
  } finally { unsubscribe(); fetch.mock.restore(); client.clear(); }
});

test('returning to All Listings restores every cached page without another request', async () => {
  const client = createQueryClient();
  const fetch = mock.method(globalThis, 'fetch', async (url: string) => response(url.includes('cursor=') ? page([2]) : page([1], 'second')));
  const observer = new InfiniteQueryObserver(client, listingsQuery());
  const unsubscribe = observer.subscribe(() => {});
  try {
    await observer.refetch();
    await observer.fetchNextPage();
    unsubscribe();
    const cached = await client.fetchInfiniteQuery(listingsQuery());
    assert.deepEqual(listingItems(cached.pages).map(item => item.job.id), [1, 2]);
    assert.equal(fetch.mock.callCount(), 2);
    const revisited = new InfiniteQueryObserver(client, listingsQuery());
    assert.deepEqual(listingItems(revisited.getCurrentResult().data!.pages).map(item => item.job.id), [1, 2]);
  } finally { unsubscribe(); fetch.mock.restore(); client.clear(); }
});

test('filter keys separate every selector while equivalent normalized searches share their cache', () => {
  const base = emptyFilters();
  assert.deepEqual(queryKeys.listingPages({ ...base, query: ' Python ' }), queryKeys.listingPages({ ...base, query: 'python' }));
  for (const [key, value] of Object.entries({ query: 'backend', location: 'Toronto', term: 'Summer 2027', source: '2', status: 'open' })) {
    assert.notDeepEqual(queryKeys.listingPages(base), queryKeys.listingPages({ ...base, [key]: value }));
  }
});

test('changing filters cancels stale pages and keeps the new query independent', async () => {
  const client = createQueryClient();
  let staleSignal: AbortSignal | undefined;
  const fetch = mock.method(globalThis, 'fetch', (url: string, options?: RequestInit) => {
    if (url.includes('cursor=')) {
      staleSignal = options?.signal ?? undefined;
      return new Promise<Response>((_, reject) => staleSignal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError'))));
    }
    return Promise.resolve(response(url.includes('q=python') ? page([9]) : page([1], 'old')));
  });
  const observer = new InfiniteQueryObserver(client, listingsQuery());
  const unsubscribe = observer.subscribe(() => {});
  try {
    await observer.refetch();
    const pending = observer.fetchNextPage();
    unsubscribe();
    assert.equal(staleSignal?.aborted, true);
    const fresh = await client.fetchInfiniteQuery(listingsQuery({ ...emptyFilters(), query: 'python' }));
    await pending;
    assert.deepEqual(listingItems(fresh.pages).map(item => item.job.id), [9]);
    assert.deepEqual(fresh.pageParams, [null]);
  } finally { unsubscribe(); fetch.mock.restore(); client.clear(); }
});

test('background refresh preserves previously fetched directory pages', async () => {
  const client = createQueryClient();
  const options = { ...listingsQuery(), retry: false };
  const cached = { pages: [page([1], 'next'), page([2])], pageParams: [null, 'next'] };
  client.setQueryData(options.queryKey, cached);
  let reject!: (reason: Error) => void;
  const fetch = mock.method(globalThis, 'fetch', () => new Promise<Response>((_, fail) => { reject = fail; }));
  const observer = new InfiniteQueryObserver(client, options);
  const unsubscribe = observer.subscribe(() => {});
  try {
    const refresh = observer.refetch();
    assert.deepEqual(observer.getCurrentResult().data, cached);
    reject(new Error('Offline'));
    await refresh;
    assert.deepEqual(observer.getCurrentResult().data, cached);
  } finally { unsubscribe(); fetch.mock.restore(); client.clear(); }
});

test('pagination controls expose keyboard buttons, concise loading status, and inline retry', () => {
  const props = { count: 50, total: 123, hasMore: true, loading: false, error: false, onMore() {} };
  assert.match(renderToStaticMarkup(createElement(CollectionPagination, props)), /Showing 50 of 123 jobs.*<button[^>]*>Show more/s);
  const loading = renderToStaticMarkup(createElement(CollectionPagination, { ...props, loading: true }));
  assert.match(loading, /role="status"[^>]*>Loading more/);
  const error = renderToStaticMarkup(createElement(CollectionPagination, { ...props, error: true }));
  assert.match(error, /Could not load more jobs/);
  assert.match(error, /<button[^>]*>Try again/);
});
