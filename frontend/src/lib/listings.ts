// Normalize directory query keys and combine fetched pages without repeating canonical jobs.
import type { JobListPage, LocationRegion, RegionName } from '../types/jobs';
export interface ListingFilters { query: string; region: string; area: string; location: string; term: string; source: string; status: string; }
export const emptyFilters = (): ListingFilters => ({ query: '', region: '', area: '', location: '', term: '', source: '', status: '' });
export const REGION_NAMES: RegionName[] = ['Canada', 'United States', 'International', 'Remote'];
const AREA_PLACEHOLDERS: Record<RegionName, string> = {
  Canada: 'All provinces', 'United States': 'All states', International: 'All countries', Remote: 'All remote',
};
export const areaPlaceholder = (region: string) => AREA_PLACEHOLDERS[region as RegionName] ?? 'Province / state';
// Region > province/state/country (Remote (X) included) > city; each level narrows the next.
export const areasFor = (regions: LocationRegion[], region: string) => regions.find(item => item.name === region)?.areas ?? [];
export const citiesFor = (regions: LocationRegion[], region: string, area: string) => areasFor(regions, region).find(item => item.name === area)?.cities ?? [];
export const chooseRegion = (filters: ListingFilters, region: string): ListingFilters => ({ ...filters, region, area: '', location: '' });
export const chooseArea = (filters: ListingFilters, area: string): ListingFilters => ({ ...filters, area, location: '' });
export const normalizeFilters = (filters: ListingFilters): ListingFilters => ({ ...filters, query: filters.query.trim().toLowerCase() });
export const listingItems = (pages: JobListPage[]) => [...new Map(pages.flatMap(page => page.items).map(item => [item.job.id, item])).values()];
