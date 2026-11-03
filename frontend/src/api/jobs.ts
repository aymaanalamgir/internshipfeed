// Fetch job details, source configuration, and ingestion progress; start listing updates.
import { requestJson } from './http';
import type { JobDetails, JobSource, SyncRun } from '../types/jobs';

export const getListings = (signal?: AbortSignal) => requestJson<JobDetails[]>('/api/feed', { signal });
export const getJobDetails = (id: number, signal?: AbortSignal) => requestJson<JobDetails>(`/api/jobs/${id}/details`, { signal });
export const getSources = (signal?: AbortSignal) => requestJson<JobSource[]>('/api/sources', { signal });
export const getLatestSync = (signal?: AbortSignal) => requestJson<SyncRun | null>('/api/ingestion-batches/latest', { signal });
export const updateListings = () => requestJson<SyncRun>('/api/sync', { method: 'POST' });
