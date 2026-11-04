// Edit a submitted application's status only after the backend confirms the change.
import { useState } from 'react';
import { updateApplication } from '../api/applications';
import { APPLICATION_STATUSES } from '../lib/applications';
import type { ApplicationStatus, JobApplication } from '../types/jobs';

export function ApplicationStatusControl({ application, disabled, onUpdated }: {
  application: JobApplication; disabled: boolean; onUpdated: () => void;
}) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  async function change(status: ApplicationStatus) {
    if (saving || status === application.status) return;
    setSaving(true); setError('');
    try {
      await updateApplication(application.canonical_job_id, status);
      onUpdated();
    } catch {
      setError('Could not update status. Please try again.');
    } finally { setSaving(false); }
  }

  return <div className="application-status" aria-busy={saving}>
    <label htmlFor={`application-status-${application.canonical_job_id}`}>Status
      <select id={`application-status-${application.canonical_job_id}`} value={application.status} disabled={disabled || saving}
        onChange={event => void change(event.target.value as ApplicationStatus)}>
        {APPLICATION_STATUSES.map(([status, label]) => <option key={status} value={status}>{label}</option>)}
      </select>
    </label>
    {saving && <span className="muted" role="status">Saving…</span>}
    {error && <p className="field-error" role="alert">{error}</p>}
  </div>;
}
