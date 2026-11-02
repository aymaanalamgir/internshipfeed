import type { Recommendation } from '../types/jobs';
import { requestJson } from './http';

export function getRecommendations(signal?: AbortSignal): Promise<Recommendation[]> {
  return requestJson('/api/recommendations', { signal });
}
