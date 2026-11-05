// Render the neutral job directory with filters, source updates, details, and actions.
import { useEffect } from 'react';
import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { listingFiltersQuery, listingsQuery } from '../lib/queries';
import { LoadingState, RefreshState } from '../components/LoadingState';
import { PostedDate } from '../components/PostedDate';
import type { JobSource } from '../types/jobs';
import type { JobWorkflow } from '../hooks/useJobActions';
import { mergeInteractionStates, emptyInteraction } from '../lib/workflow';
import { listingItems } from '../lib/listings';
import type { ListingFilters } from '../lib/listings';
import { useDebouncedValue } from '../hooks/useDebouncedValue';
import { CollectionPagination } from '../components/CollectionPagination';
import { FilterBar } from '../components/FilterBar';
import { JobActions } from '../components/JobActions';
import { SkillTag } from '../components/SkillTag';
import { SyncControl } from '../components/SyncControl';

export function ListingsPage({ workflow, sources, filters, onFiltersChange, onUpdated, onOpenJob }: {
  workflow: JobWorkflow; sources: JobSource[]; onUpdated: () => void; onOpenJob: (id: number) => void;
  filters: ListingFilters; onFiltersChange: (filters: ListingFilters) => void;
}) {
  const search = useDebouncedValue(filters.query);
  const query = useInfiniteQuery(listingsQuery({ ...filters, query: search }));
  const options = useQuery(listingFiltersQuery());
  const jobs = listingItems(query.data?.pages ?? []);
  const loading = query.data === undefined;
  const total = query.data?.pages.at(-1)?.total ?? 0;
  const incoming = jobs.map(item => item.interaction_state);
  const { install } = workflow;
  useEffect(() => { if (query.data) install(query.data.pages.flatMap(page => page.items.map(item => item.interaction_state))); }, [query.data, install]);
  const states = mergeInteractionStates(workflow.states, incoming);
  const visible = jobs.filter(item => !states[item.job.id]?.dismissed && !states[item.job.id]?.applied);
  return <main className="workspace listings-page" id="main-content">
    <div className="page-heading"><h1 tabIndex={-1}>All listings</h1><button id="reload" className="text-button" title="Refetch stored jobs; does not scrape sources" disabled={query.isFetching} onClick={() => void query.refetch()}>Refresh data</button></div>
    <FilterBar options={options.data ?? { locations: [], terms: [] }} sources={sources} filters={filters} onChange={onFiltersChange} />
    <div className="directory-meta"><span id="summary">{loading ? 'Loading listings…' : `${visible.length} of ${total} jobs`}</span><SyncControl onUpdated={onUpdated} /></div>
    {options.isError && <p className="field-error" role="alert">Could not load filter options. <button className="text-button" onClick={() => void options.refetch()}>Retry filters</button></p>}
    {!loading && <RefreshState fetching={query.isFetching && !query.isFetchingNextPage} error={query.isError && !query.isFetchNextPageError} onRetry={() => void query.refetch()} />}
    {loading && !query.isError ? <LoadingState label="Loading listings…" />
      : loading && query.isError ? <div className="load-error" role="alert"><p>Could not load listings.</p><button className="button" onClick={() => void query.refetch()}>Try again</button></div>
      : !visible.length ? <p className="empty-state">No listings match these filters.</p>
      : <><div className="listing-list" id="jobs">{visible.map(item => <article className="listing-row" data-job-id={item.job.id} key={item.job.id}>
        <div className="listing-title"><p className="job-company">{item.job.company}</p><h2><button className="job-title" onClick={() => onOpenJob(item.job.id)}>{item.job.title}</button></h2>
          <div className="skill-tags">{item.skills.slice(0, 3).map(skill => <SkillTag key={skill} label={skill} />)}</div><PostedDate date={item.job.posted_at} /></div>
        <p className="listing-location">{item.job.location}<span>{item.term || 'Term not specified'}</span>{item.job.status === 'unknown' && <span>Availability unconfirmed</span>}</p>
        <div className="listing-controls"><JobActions job={item.job} state={states[item.job.id] ?? emptyInteraction(item.job.id)}
          pending={workflow.pending[item.job.id]} error={workflow.errors[item.job.id]} onApply={workflow.apply}
          onSave={(id, type) => void workflow.act(id, type)} onApplied={(id, type) => void workflow.act(id, type)} onDismiss={(id, type) => void workflow.act(id, type)} /></div>
      </article>)}</div><CollectionPagination count={visible.length} total={total} hasMore={query.hasNextPage}
        loading={query.isFetchingNextPage} error={query.isFetchNextPageError} onMore={() => void query.fetchNextPage()} /></>}
  </main>;
}
