// Normalize directory query keys and combine fetched pages without repeating canonical jobs.
import type { JobListPage } from '../types/jobs';
export interface ListingFilters { query: string; location: string; term: string; source: string; status: string; }
export const emptyFilters = (): ListingFilters => ({ query: '', location: '', term: '', source: '', status: '' });
export const normalizeFilters = (filters: ListingFilters): ListingFilters => ({ ...filters, query: filters.query.trim().toLowerCase() });
export const listingItems = (pages: JobListPage[]) => [...new Map(pages.flatMap(page => page.items).map(item => [item.job.id, item])).values()];
