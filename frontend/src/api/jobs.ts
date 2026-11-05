// Fetch job details, source configuration, and ingestion progress; start listing updates.
import { requestJson } from './http';
import type { IngestionStatus, JobDetails, JobListPage, JobSource, ListingFilterOptions, SyncRun } from '../types/jobs';
import { emptyFilters, normalizeFilters } from '../lib/listings';
import type { ListingFilters } from '../lib/listings';

export const LISTING_PAGE_SIZE = 50;
export function getListings(filters: ListingFilters = emptyFilters(), cursor: string | null = null, signal?: AbortSignal) {
  const values = normalizeFilters(filters);
  const params = new URLSearchParams({ limit: String(LISTING_PAGE_SIZE) });
  for (const [key, value] of Object.entries({ q: values.query, location: values.location, term: values.term, source_id: values.source, status: values.status, cursor })) {
    if (value) params.set(key, value);
  }
  return requestJson<JobListPage>(`/api/feed?${params}`, { signal });
}
export const getListingFilters = (signal?: AbortSignal) => requestJson<ListingFilterOptions>('/api/feed/filters', { signal });
export const getJobDetails = (id: number, signal?: AbortSignal) => requestJson<JobDetails>(`/api/jobs/${id}/details`, { signal });
export const getSources = (signal?: AbortSignal) => requestJson<JobSource[]>('/api/sources', { signal });
export const getLatestSync = (signal?: AbortSignal) => requestJson<SyncRun | null>('/api/ingestion-batches/latest', { signal });
export const updateListings = () => requestJson<SyncRun>('/api/sync', { method: 'POST' });
export const getIngestionStatus = (signal?: AbortSignal) => requestJson<IngestionStatus>('/api/ingestion-status', { signal });
