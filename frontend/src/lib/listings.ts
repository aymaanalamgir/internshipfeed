import type { JobDetails } from '../types/jobs';
export interface ListingFilters { query: string; location: string; term: string; source: string; status: string; }
export const emptyFilters = (): ListingFilters => ({ query: '', location: '', term: '', source: '', status: '' });
export function filterListings(jobs: JobDetails[], filters: ListingFilters): JobDetails[] {
  const query = filters.query.trim().toLocaleLowerCase();
  return jobs.filter(item => {
    const text = [item.job.company, item.job.title, item.job.location, item.term,
      ...Object.values(item.keywords).flat(), ...item.sources.flatMap(source => [source.title, source.company ?? '', source.location, source.term])].join(' ').toLocaleLowerCase();
    return (!query || text.includes(query)) && (!filters.location || item.job.location === filters.location)
      && (!filters.term || item.term === filters.term) && (!filters.status || item.job.status === filters.status)
      && (!filters.source || item.sources.some(source => String(source.source_id) === filters.source && !['closed', 'dead'].includes(source.status)));
  });
}
