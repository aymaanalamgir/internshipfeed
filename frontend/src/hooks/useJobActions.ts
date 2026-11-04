// Coordinate confirmed interaction snapshots, pending requests, errors, and announcements.
import { useCallback, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { invalidateJobs } from '../lib/queries';
import { recordInteraction } from '../api/interactions';
import { applicationUrl, confirmInteraction, mergeInteractionStates } from '../lib/workflow';
import type { CanonicalJob, InteractionState, InteractionStates, InteractionType } from '../types/jobs';

export function useJobActions() {
  const client = useQueryClient();
  const [application, setApplication] = useState<CanonicalJob | null>(null);
  const [states, setStates] = useState<InteractionStates>({});
  const [pending, setPending] = useState<Record<number, InteractionType | undefined>>({});
  const [errors, setErrors] = useState<Record<number, string>>({});
  const [announcement, setAnnouncement] = useState('');
  const busy = useRef(new Set<number>());
  const install = useCallback((incoming: InteractionState[]) => setStates(current => mergeInteractionStates(current, incoming)), []);

  async function act(id: number, type: Exclude<InteractionType, 'click'>) {
    if (busy.current.has(id)) return false;
    busy.current.add(id);
    setPending(current => ({ ...current, [id]: type }));
    setErrors(current => ({ ...current, [id]: '' }));
    try {
      const receipt = await recordInteraction(id, type);
      setStates(current => confirmInteraction(current, id, receipt));
      const messages = { save: 'Job saved.', unsave: 'Job removed from Saved.', dismiss: 'Job dismissed.', undismiss: 'Job restored.', applied: 'Marked as applied.', unapplied: 'Marked as not applied.' };
      setAnnouncement(messages[type]);
      void invalidateJobs(client);
      return true;
    } catch {
      const actions = { save: 'save this job', unsave: 'unsave this job', dismiss: 'dismiss this job', undismiss: 'restore this job', applied: 'mark this job as applied', unapplied: 'unmark this job as applied' };
      const action = actions[type];
      setErrors(current => ({ ...current, [id]: `Could not ${action}. Please try again.` }));
      return false;
    } finally {
      busy.current.delete(id);
      setPending(current => ({ ...current, [id]: undefined }));
    }
  }
  function closeApplication(id: number) {
    setApplication(current => current?.id === id ? null : current);
  }
  function apply(job: CanonicalJob) {
    if (!applicationUrl(job.application_url)) return;
    const id = job.id;
    if (!states[id]?.applied) setApplication(job);
    setErrors(current => ({ ...current, [id]: '' }));
    void recordInteraction(id, 'click').then(receipt => {
      setStates(current => confirmInteraction(current, id, receipt));
      void invalidateJobs(client);
    })
      .catch(() => setErrors(current => ({ ...current, [id]: 'Click could not be recorded. The application link is still available.' })));
  }
  return { application, closeApplication, states, install, pending, errors, announcement, act, apply };
}
export type JobWorkflow = ReturnType<typeof useJobActions>;
