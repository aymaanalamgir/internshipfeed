// Render application links and confirmed Save, Applied, Dismiss, and Restore controls.
import type { AppliedAction, DismissAction, CanonicalJob, InteractionState, InteractionType, SaveAction } from '../types/jobs';
import { applicationUrl } from '../lib/workflow';
import { Icon } from './Icon';

export interface JobActionsProps {
  job: CanonicalJob; state: InteractionState; pending?: InteractionType; error?: string; appliedView?: boolean; showAppliedAction?: boolean;
  onApply: (job: CanonicalJob) => void; onSave: (id: number, type: SaveAction) => void; onApplied: (id: number, type: AppliedAction) => void; onDismiss: (id: number, type: DismissAction) => void;
}
export function JobActions({ job, state, pending, error, appliedView, showAppliedAction, onApply, onSave, onApplied, onDismiss }: JobActionsProps) {
  const url = applicationUrl(job.application_url);
  return <><div className="job-actions recommendation-actions">
    {url ? <a className="button button-primary apply-action" href={url} target="_blank" rel="noopener noreferrer" onClick={() => onApply(job)}>
      {state.applied ? 'Open application' : 'Apply'}<Icon name="external" /></a>
      : null}
    <button className={`button utility-action${state.saved ? ' is-active' : ''}`} aria-label={state.saved ? 'Unsave job' : 'Save job'}
      title={state.saved ? 'Unsave job' : 'Save job'} aria-pressed={state.saved} disabled={Boolean(pending)} onClick={() => onSave(job.id, state.saved ? 'unsave' : 'save')}>
      <Icon name="bookmark" filled={state.saved} />{pending === 'save' ? 'Saving…' : pending === 'unsave' ? 'Removing…' : state.saved ? 'Saved' : 'Save'}</button>
    {(showAppliedAction || state.applied) && <button className={`button utility-action${state.applied ? ' is-active' : ''}`} aria-label={state.applied ? 'Unmark job as applied' : 'Mark job as applied'}
      title={state.applied ? 'Unmark as applied' : 'Mark as applied'} aria-pressed={state.applied} disabled={Boolean(pending)} onClick={() => onApplied(job.id, state.applied ? 'unapplied' : 'applied')}>
      <Icon name="clock" />{pending === 'applied' ? 'Recording…' : pending === 'unapplied' ? 'Updating…' : state.applied ? 'Applied' : 'Mark applied'}</button>}
    {state.dismissed ? <button className="button utility-action" aria-label="Restore job" title="Restore job" disabled={Boolean(pending)}
      onClick={() => onDismiss(job.id, 'undismiss')}><Icon name="refresh" />{pending === 'undismiss' ? 'Restoring…' : 'Restore'}</button>
      : !appliedView && <button className="icon-button dismiss-action" aria-label="Dismiss job" title="Dismiss job" disabled={Boolean(pending)}
        onClick={() => onDismiss(job.id, 'dismiss')}><Icon name="close" /></button>}
  </div>{error && <p className="field-error action-error" role="alert">{error}</p>}</>;
}
