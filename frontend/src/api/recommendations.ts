// Fetch Recommended, Saved, Applied, and Dismissed collections in server order.
import type { ApplicationStatus, CandidateJob, RankedRecommendation } from '../types/jobs';
import { requestJson } from './http';

export function getRecommendations(signal?: AbortSignal, offset = 0): Promise<RankedRecommendation[]> {
  return requestJson(`/api/recommendations?limit=30&offset=${offset}`, { signal });
}

export function getSavedJobs(signal?: AbortSignal, offset = 0): Promise<CandidateJob[]> {
  return requestJson(`/api/saved?limit=50&offset=${offset}`, { signal });
}

export function getAppliedJobs(signal?: AbortSignal, status?: ApplicationStatus, offset = 0): Promise<CandidateJob[]> {
  return requestJson(`/api/applied?limit=50&offset=${offset}` + (status ? `&status=${encodeURIComponent(status)}` : ''), { signal });
}

export function getDismissedJobs(signal?: AbortSignal, offset = 0): Promise<CandidateJob[]> {
  return requestJson(`/api/dismissed?limit=50&offset=${offset}`, { signal });
}
