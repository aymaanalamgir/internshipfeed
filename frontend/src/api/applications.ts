// Update submitted applications through the candidate-scoped application API.
import type { ApplicationStatus, JobApplication } from '../types/jobs';
import { requestJson } from './http';

export function updateApplication(jobId: number, status: ApplicationStatus): Promise<JobApplication> {
  return requestJson(`/api/jobs/${jobId}/application`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status }),
  });
}
