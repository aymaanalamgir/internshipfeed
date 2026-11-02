import type { CandidateJob, RankedRecommendation } from '../types/jobs';
import { requestJson } from './http';

export function getRecommendations(signal?: AbortSignal): Promise<RankedRecommendation[]> {
  return requestJson('/api/recommendations', { signal });
}

export function getSavedJobs(signal?: AbortSignal): Promise<CandidateJob[]> {
  return requestJson('/api/saved', { signal });
}

export function getAppliedJobs(signal?: AbortSignal): Promise<CandidateJob[]> {
  return requestJson('/api/applied', { signal });
}
