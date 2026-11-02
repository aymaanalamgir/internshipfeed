import { useState } from 'react';
import type { FormEvent } from 'react';
import type { CandidateProfile, TagField } from '../types/profile';
import { updateProfile } from '../api/profile';
import { buildProfilePayload, emptyPending, graduationYearError, toDraft } from '../lib/form';
import { TagInput } from './TagInput';

const FIELDS: { key: TagField; label: string; placeholder: string }[] = [
  { key: 'target_roles', label: 'Target roles', placeholder: 'Add a role…' },
  { key: 'skills', label: 'Skills', placeholder: 'Add a skill…' },
  { key: 'preferred_locations', label: 'Preferred locations', placeholder: 'Add a location…' },
  { key: 'preferred_terms', label: 'Preferred terms', placeholder: 'Add a term…' },
];

export function ProfileForm({ profile }: { profile: CandidateProfile }) {
  const [baseline, setBaseline] = useState(profile);
  const [draft, setDraft] = useState(() => toDraft(profile));
  const [pending, setPending] = useState(emptyPending);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState('');
  const [yearError, setYearError] = useState<string>();
  const dirty = JSON.stringify(draft) !== JSON.stringify(toDraft(baseline)) || Object.values(pending).some(value => value.trim());

  function edited() { setSaved(false); setError(''); }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const validation = graduationYearError(draft.graduation_year);
    setYearError(validation);
    if (validation) { document.getElementById('graduation-year')?.focus(); return; }
    setSaving(true);
    setSaved(false);
    setError('');
    try {
      // Include uncommitted input so clicking Save never drops a typed preference.
      const stored = await updateProfile(buildProfilePayload(draft, pending));
      setBaseline(stored);
      setDraft(toDraft(stored));
      setPending(emptyPending());
      setSaved(true);
    } catch {
      setError('Could not save changes. Please try again.');
    } finally { setSaving(false); }
  }

  return (
    <form onSubmit={submit} noValidate aria-label="Candidate profile" aria-busy={saving}>
      <div className="form-intro">
        <span className="section-label">Your preferences</span>
        <span id="tag-instructions">Type a value, then press Enter.</span>
      </div>
      <fieldset disabled={saving} className="profile-fields">
        <legend className="sr-only">Internship preferences</legend>
        {FIELDS.map(field => (
          <TagInput key={field.key} id={field.key} label={field.label} placeholder={field.placeholder}
            disabled={saving} values={draft[field.key]} input={pending[field.key]}
            onChange={values => { setDraft(current => ({ ...current, [field.key]: values })); edited(); }}
            onInput={value => { setPending(current => ({ ...current, [field.key]: value })); edited(); }} />
        ))}
        <div className="profile-field year-field">
          <label htmlFor="graduation-year">Graduation year <span className="optional">Optional</span></label>
          <input id="graduation-year" type="number" inputMode="numeric" min="1900" max="2200" step="1"
            value={draft.graduation_year} placeholder="e.g. 2027" aria-invalid={Boolean(yearError)}
            aria-describedby={yearError ? 'year-error' : undefined}
            onBlur={() => setYearError(graduationYearError(draft.graduation_year))}
            onChange={event => { setDraft(current => ({ ...current, graduation_year: event.target.value })); setYearError(undefined); edited(); }} />
          {yearError && <p className="field-error" id="year-error" role="alert">{yearError}</p>}
        </div>
      </fieldset>
      <div className="form-actions">
        <div className="save-message" aria-live="polite">
          {error ? <span className="field-error" role="alert">{error}</span>
            : saved ? <span className="saved"><span aria-hidden="true">✓</span> Saved</span>
              : dirty ? <span>Unsaved changes</span> : <span>Up to date</span>}
        </div>
        <button className="button button-primary" type="submit" disabled={saving || !dirty}>
          {saving ? 'Saving…' : 'Save profile'}<span aria-hidden="true">↗</span>
        </button>
      </div>
    </form>
  );
}
