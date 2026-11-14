// Expose search and metadata filters for the neutral job directory.
import type { JobSource, ListingFilterOptions } from '../types/jobs';
import { REGION_NAMES, areaPlaceholder, areasFor, chooseArea, chooseRegion, citiesFor } from '../lib/listings';
import type { ListingFilters } from '../lib/listings';
// Keep a selection visible even when it is no longer offered (e.g. after a sync).
const withSelection = (values: string[], selected: string) => selected && !values.includes(selected) ? [...values, selected] : values;
export function FilterBar({ options, sources, filters, onChange }: { options: ListingFilterOptions; sources: JobSource[]; filters: ListingFilters; onChange: (filters: ListingFilters) => void }) {
  const present = options.regions.map(region => region.name);
  const regions = withSelection(REGION_NAMES.filter(name => !present.length || present.includes(name)), filters.region);
  const areas = withSelection(areasFor(options.regions, filters.region).map(area => area.name), filters.area);
  const cities = withSelection(citiesFor(options.regions, filters.region, filters.area), filters.location);
  const terms = withSelection(options.terms, filters.term);
  const change = (key: keyof ListingFilters, value: string) => onChange({ ...filters, [key]: value });
  return <div className="filter-bar" role="search" aria-label="Filter listings">
    <label className="search-control"><span className="sr-only">Search listings</span><input id="search" type="search" value={filters.query} maxLength={200}
      placeholder="Search jobs, companies, skills…" onChange={event => change('query', event.target.value)} /></label>
    <label><span className="sr-only">Region</span><select aria-label="Region" value={filters.region} onChange={event => onChange(chooseRegion(filters, event.target.value))}>
      <option value="">All regions</option>{regions.map(value => <option key={value}>{value}</option>)}</select></label>
    <label><span className="sr-only">Province, state or country</span><select aria-label="Province, state or country" value={filters.area} disabled={!filters.region}
      onChange={event => onChange(chooseArea(filters, event.target.value))}>
      <option value="">{areaPlaceholder(filters.region)}</option>{areas.map(value => <option key={value}>{value}</option>)}</select></label>
    <label><span className="sr-only">City</span><select aria-label="City" value={filters.location} disabled={!cities.length} onChange={event => change('location', event.target.value)}>
      <option value="">{filters.area && !cities.length ? 'No cities' : 'All cities'}</option>{cities.map(value => <option key={value}>{value}</option>)}</select></label>
    <label><span className="sr-only">Term</span><select aria-label="Term" value={filters.term} onChange={event => change('term', event.target.value)}><option value="">All terms</option>{terms.map(value => <option key={value}>{value}</option>)}</select></label>
    <label><span className="sr-only">Source</span><select aria-label="Source" id="source" value={filters.source} onChange={event => change('source', event.target.value)}><option value="">All sources</option>{sources.map(source => <option key={source.id} value={source.id}>{source.name}</option>)}</select></label>
    <label><span className="sr-only">Status</span><select aria-label="Status" id="status" value={filters.status} onChange={event => change('status', event.target.value)}><option value="">Any status</option><option value="open">Open</option><option value="unknown">Unconfirmed</option></select></label>
  </div>;
}
