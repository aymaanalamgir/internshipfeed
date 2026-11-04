// Confirm application outcomes through existing interactions without blocking external navigation.
import type { JobWorkflow } from '../hooks/useJobActions';
import { emptyInteraction } from '../lib/workflow';
import { Drawer } from './Drawer';

export function ApplyConfirmation({ workflow }: { workflow: JobWorkflow }) {
  const job = workflow.application!;
  const state = workflow.states[job.id] ?? emptyInteraction(job.id);
  const pending = workflow.pending[job.id];
  const close = () => workflow.closeApplication(job.id);

  async function choose(type: 'applied' | 'save') {
    if (pending) return;
    if (state[type === 'save' ? 'saved' : 'applied'] || await workflow.act(job.id, type)) close();
  }

  return <Drawer title="Did you apply for this role?" kind="apply" onClose={close}>
    <p className="confirmation-job">{job.company} · {job.title}</p>
    <div className="confirmation-choices" aria-busy={Boolean(pending)}>
      <button type="button" className="confirmation-choice" aria-label="Yes, I applied" disabled={Boolean(pending)} onClick={() => void choose('applied')}>
        <span>{pending === 'applied' ? 'Recording…' : 'Yes, I applied'}</span><small>I submitted an application.</small>
      </button>
      <button type="button" className="confirmation-choice" aria-label="Save for later" disabled={Boolean(pending)} onClick={() => void choose('save')}>
        <span>{pending === 'save' ? 'Saving…' : 'Save for later'}</span><small>I want to come back to this.</small>
      </button>
      <button type="button" className="confirmation-choice" aria-label="Keep in feed" onClick={close}>
        <span>Keep in feed</span><small>I'm still deciding.</small>
      </button>
    </div>
    {workflow.errors[job.id] && <p className="field-error confirmation-error" role="alert">{workflow.errors[job.id]}</p>}
  </Drawer>;
}
