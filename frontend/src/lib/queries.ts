// Keep candidate queries in each browser's current-session cache and share invalidation policies.
import { QueryClient, queryOptions } from '@tanstack/react-query';
import { getJobDetails, getListings, getSources } from '../api/jobs';
import { getInteractionStates } from '../api/interactions';
import { getAppliedJobs, getDismissedJobs, getRecommendations, getSavedJobs } from '../api/recommendations';
import { getProfile } from '../api/profile';
import { getLatestResume, getSkillSuggestions } from '../api/resumes';
import type { ApplicationStatus, CandidateJob, JobView } from '../types/jobs';

export const createQueryClient = () => new QueryClient({ defaultOptions: { queries: {
  staleTime: 45_000, gcTime: 5 * 60_000, refetchOnWindowFocus: false, retry: 1,
} } });

// Each page owns its QueryClient; the API resolves its HttpOnly session without client-supplied IDs.
export const queryKeys = {
  jobs: ['jobs', 'current-session'] as const,
  collection: (view: JobView, status: ApplicationStatus | '' = '') => ['jobs', 'current-session', view, view === 'applied' ? status : ''] as const,
  listings: ['jobs', 'current-session', 'listings'] as const,
  details: (id: number) => ['jobs', 'current-session', 'details', id] as const,
  profile: ['profile', 'current-session'] as const,
  resume: ['resume', 'current-session', 'latest'] as const,
  suggestions: (id: number | null) => ['resume', 'current-session', 'skills', id] as const,
  sources: ['sources'] as const,
};

export const collectionQuery = (view: JobView, status: ApplicationStatus | '' = '') => queryOptions<CandidateJob[]>({
  queryKey: queryKeys.collection(view, status),
  queryFn: ({ signal }) => view === 'applied' ? getAppliedJobs(signal, status || undefined)
    : ({ recommended: getRecommendations, saved: getSavedJobs, dismissed: getDismissedJobs })[view](signal),
});
export const listingsQuery = () => queryOptions({ queryKey: queryKeys.listings, queryFn: async ({ signal }) => {
  const [jobs, states] = await Promise.all([getListings(signal), getInteractionStates(signal)]);
  return { jobs, states };
} });
export const detailsQuery = (id: number) => queryOptions({ queryKey: queryKeys.details(id), queryFn: ({ signal }) => getJobDetails(id, signal) });
export const profileQuery = () => queryOptions({ queryKey: queryKeys.profile, queryFn: ({ signal }) => getProfile(signal) });
export const resumeQuery = () => queryOptions({ queryKey: queryKeys.resume, queryFn: ({ signal }) => getLatestResume(signal) });
export const suggestionsQuery = (id: number | null) => queryOptions({ queryKey: queryKeys.suggestions(id),
  enabled: id !== null, queryFn: ({ signal }) => getSkillSuggestions(id!, signal) });
export const sourcesQuery = () => queryOptions({ queryKey: queryKeys.sources, queryFn: ({ signal }) => getSources(signal) });

export const invalidateJobs = (client: QueryClient) => client.invalidateQueries({ queryKey: queryKeys.jobs });
