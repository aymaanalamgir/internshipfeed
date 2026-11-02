import type { InteractionEvent, InteractionState, InteractionStates, Recommendation } from '../types/jobs';

export function emptyInteraction(jobId: number): InteractionState {
  return { job_id: jobId, saved: false, dismissed: false, applied: false, last_interaction_at: null };
}

export function indexInteractions(states: InteractionState[]): InteractionStates {
  return Object.fromEntries(states.map(state => [state.job_id, state]));
}

export function confirmInteraction(states: InteractionStates, requestedId: number, event: InteractionEvent): InteractionStates {
  const old = states[requestedId] ?? emptyInteraction(requestedId);
  const canonical = states[event.canonical_job_id] ?? emptyInteraction(event.canonical_job_id);
  const times = [old.last_interaction_at, canonical.last_interaction_at, event.created_at]
    .filter((value): value is string => value !== null)
    .sort((left, right) => Date.parse(right) - Date.parse(left));
  const state = {
    saved: old.saved || canonical.saved || event.interaction_type === 'save',
    dismissed: old.dismissed || canonical.dismissed || event.interaction_type === 'dismiss',
    applied: old.applied || canonical.applied || event.interaction_type === 'applied',
    last_interaction_at: times[0],
  };
  // A stale displayed ID may have merged while the request was in flight.
  return { ...states, [requestedId]: { ...state, job_id: requestedId },
    [event.canonical_job_id]: { ...state, job_id: event.canonical_job_id } };
}

export function visibleRecommendations(recommendations: Recommendation[], states: InteractionStates): Recommendation[] {
  return recommendations.filter(({ job }) => !states[job.id]?.dismissed && !['closed', 'dead'].includes(job.status));
}

export function applicationUrl(value: string | null): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}
