// Expose search and metadata filters for the neutral job directory.
import type { JobDetails, JobSource } from '../types/jobs';
import type { ListingFilters } from '../lib/listings';
export function FilterBar({ jobs, sources, filters, onChange }: { jobs: JobDetails[]; sources: JobSource[]; filters: ListingFilters; onChange: (filters: ListingFilters) => void }) {
  const locations = [...new Set(jobs.map(item => item.job.location))].sort();
  const terms = [...new Set(jobs.map(item => item.term).filter((term): term is string => Boolean(term)))].sort();
  const change = (key: keyof ListingFilters, value: string) => onChange({ ...filters, [key]: value });
  return <div className="filter-bar" role="search" aria-label="Filter listings">
    <label className="search-control"><span className="sr-only">Search listings</span><input id="search" type="search" value={filters.query} maxLength={200}
      placeholder="Search jobs, companies, skills…" onChange={event => change('query', event.target.value)} /></label>
    <label><span className="sr-only">Location</span><select aria-label="Location" value={filters.location} onChange={event => change('location', event.target.value)}><option value="">All locations</option>{locations.map(value => <option key={value}>{value}</option>)}</select></label>
    <label><span className="sr-only">Term</span><select aria-label="Term" value={filters.term} onChange={event => change('term', event.target.value)}><option value="">All terms</option>{terms.map(value => <option key={value}>{value}</option>)}</select></label>
    <label><span className="sr-only">Source</span><select aria-label="Source" id="source" value={filters.source} onChange={event => change('source', event.target.value)}><option value="">All sources</option>{sources.map(source => <option key={source.id} value={source.id}>{source.name}</option>)}</select></label>
    <label><span className="sr-only">Status</span><select aria-label="Status" id="status" value={filters.status} onChange={event => change('status', event.target.value)}><option value="">Any status</option><option value="open">Open</option><option value="unknown">Unconfirmed</option></select></label>
  </div>;
}
