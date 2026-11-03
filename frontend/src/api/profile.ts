// Load and explicitly save the development candidate profile.
import type { CandidateProfile, ProfileValues } from '../types/profile';
import { requestJson } from './http';

export function getProfile(signal?: AbortSignal): Promise<CandidateProfile> {
  return requestJson('/api/profile', { signal });
}

export function updateProfile(values: ProfileValues): Promise<CandidateProfile> {
  return requestJson('/api/profile', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(values),
  });
}
