import type { InteractionReceipt, InteractionState, InteractionType } from '../types/jobs';
import { requestJson } from './http';

export function getInteractionStates(signal?: AbortSignal): Promise<InteractionState[]> {
  return requestJson('/api/interactions', { signal });
}

export function recordInteraction(jobId: number, type: InteractionType): Promise<InteractionReceipt> {
  return requestJson(`/api/jobs/${jobId}/interactions`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ type }),
  });
}
