// Fetch Recommended, Saved, Applied, and Dismissed collections in server order.
import type { ApplicationStatus, CandidateJob, RankedRecommendation } from '../types/jobs';
import { requestJson } from './http';

export function getRecommendations(signal?: AbortSignal): Promise<RankedRecommendation[]> {
  return requestJson('/api/recommendations', { signal });
}

export function getSavedJobs(signal?: AbortSignal): Promise<CandidateJob[]> {
  return requestJson('/api/saved', { signal });
}

export function getAppliedJobs(signal?: AbortSignal, status?: ApplicationStatus): Promise<CandidateJob[]> {
  return requestJson('/api/applied' + (status ? `?status=${encodeURIComponent(status)}` : ''), { signal });
}

export function getDismissedJobs(signal?: AbortSignal): Promise<CandidateJob[]> {
  return requestJson('/api/dismissed', { signal });
}
