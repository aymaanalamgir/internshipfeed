import type { ResumeMetadata } from '../types/profile';
import { requestJson } from './http';

export async function uploadResume(file: File): Promise<ResumeMetadata> {
  const body = new FormData();
  body.append('file', file);
  // The browser supplies the multipart boundary. Retain metadata only, not PDF text.
  const { id, candidate_profile_id, filename, uploaded_at } = await requestJson<ResumeMetadata>('/api/resumes', {
    method: 'POST', body,
  });
  return { id, candidate_profile_id, filename, uploaded_at };
}
