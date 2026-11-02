import type { InteractionReceipt, InteractionState, InteractionStates, JobView, Recommendation } from '../types/jobs';

export function emptyInteraction(jobId: number): InteractionState {
  return { job_id: jobId, saved: false, dismissed: false, applied: false, last_interaction_at: null,
    saved_at: null, applied_at: null, last_event_id: 0 };
}

export function indexInteractions(states: InteractionState[]): InteractionStates {
  return Object.fromEntries(states.map(state => [state.job_id, state]));
}

export function confirmInteraction(states: InteractionStates, requestedId: number, receipt: InteractionReceipt): InteractionStates {
  const incoming = receipt.interaction_state;
  // Install SQL-derived snapshots, never replay event types in the browser. The
  // event cursor avoids stale responses and JavaScript timestamp precision loss.
  const snapshots = [incoming, states[requestedId], states[incoming.job_id]].filter((state): state is InteractionState => Boolean(state));
  const latest = snapshots.reduce((best, state) => state.last_event_id > best.last_event_id ? state : best);
  return { ...states, [requestedId]: { ...latest, job_id: requestedId },
    [incoming.job_id]: { ...latest, job_id: incoming.job_id } };
}

export function visibleJobs(view: JobView, jobs: Recommendation[], states: InteractionStates): Recommendation[] {
  return jobs.filter(({ job }) => {
    const state = states[job.id] ?? emptyInteraction(job.id);
    if (view === 'applied') return state.applied;
    if (view === 'saved') return state.saved && !state.dismissed;
    return !state.dismissed && !state.applied && !['closed', 'dead'].includes(job.status);
  });
}

export function applicationUrl(value: string | null): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}
