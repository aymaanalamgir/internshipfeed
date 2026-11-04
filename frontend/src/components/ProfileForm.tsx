// Manage editable profile drafts and persist changes only on explicit Save.
import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { invalidateJobs, queryKeys } from '../lib/queries';
import type { FormEvent, ReactNode } from 'react';
import type { CandidateProfile, TagField } from '../types/profile';
import { updateProfile } from '../api/profile';
import { buildProfilePayload, emptyPending, graduationYearError, toDraft } from '../lib/form';
import { TagInput } from './TagInput';
import { TermSelector } from './TermSelector';
import { GRADUATION_YEARS, availableSkills } from '../lib/profile-options';
import { SkillSuggestions } from './SkillSuggestions';
import type { SuggestionState } from './SkillSuggestions';

const FIELDS: { key: TagField; label: string; placeholder: string }[] = [
  { key: 'target_roles', label: 'Target roles', placeholder: 'Add a role…' },
  { key: 'skills', label: 'Skills', placeholder: 'Add a skill…' },
  { key: 'preferred_locations', label: 'Preferred locations', placeholder: 'Add a location…' },
];

export function ProfileForm({ profile, onSaved, children, suggestions }: { suggestions?: SuggestionState; profile: CandidateProfile; onSaved?: () => void; children?: ReactNode }) {
  const client = useQueryClient();
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
      await client.cancelQueries({ queryKey: queryKeys.profile });
      client.setQueryData(queryKeys.profile, stored);
      void invalidateJobs(client);
      setBaseline(stored);
      setDraft(toDraft(stored));
      setPending(emptyPending());
      setSaved(true);
      onSaved?.();
    } catch {
      setError('Could not save changes. Please try again.');
    } finally { setSaving(false); }
  }

  return (
    <form onSubmit={submit} noValidate aria-label="Candidate profile" aria-busy={saving}>
      <div className="sr-only">
        <span id="tag-instructions">Type a value, then press Enter.</span>
      </div>
      <fieldset disabled={saving} className="profile-fields">
        <legend className="sr-only">Internship preferences</legend>
        {FIELDS.map(field => (
          <TagInput key={field.key} id={field.key} label={field.label} placeholder={field.placeholder}
            disabled={saving} values={draft[field.key]} input={pending[field.key]}
            onChange={values => { setDraft(current => ({ ...current, [field.key]: values })); edited(); }}
            onInput={value => { setPending(current => ({ ...current, [field.key]: value })); edited(); }}>
            {field.key === 'skills' && suggestions && <SkillSuggestions state={suggestions} selected={[...draft.skills, pending.skills]}
              disabled={saving} onAdd={values => { setDraft(current => ({ ...current, skills: [...current.skills,
                ...availableSkills(values, [...current.skills, pending.skills], suggestions.data?.aliases ?? {})] })); edited(); }} />}
          </TagInput>
        ))}
        <TermSelector values={draft.preferred_terms} onChange={values => { setDraft(current => ({ ...current, preferred_terms: values })); edited(); }} />
        <div className="profile-field year-field">
          <label htmlFor="graduation-year">Graduation year <span className="optional">Optional</span></label>
          <select id="graduation-year" value={draft.graduation_year} aria-invalid={Boolean(yearError)}
            aria-describedby={yearError ? 'year-error' : undefined}
            onChange={event => { setDraft(current => ({ ...current, graduation_year: event.target.value })); setYearError(undefined); edited(); }}>
            <option value="">Not specified</option>
            {draft.graduation_year && !GRADUATION_YEARS.includes(Number(draft.graduation_year)) && <option value={draft.graduation_year} disabled>{draft.graduation_year} (saved)</option>}
            {GRADUATION_YEARS.map(year => <option key={year} value={year}>{year}</option>)}
          </select>
          {yearError && <p className="field-error" id="year-error" role="alert">{yearError}</p>}
        </div>
      </fieldset>
      {children}
      <div className="form-actions">
        <div className="save-message" aria-live="polite">
          {error ? <span className="field-error" role="alert">{error}</span>
            : saved ? <span className="saved"><span aria-hidden="true">✓</span> Saved</span>
              : dirty ? <span>Unsaved changes</span> : <span>Up to date</span>}
        </div>
        <button className="button button-primary" type="submit" disabled={saving || !dirty}>
          {saving ? 'Saving…' : 'Save changes'}
        </button>
      </div>
    </form>
  );
}
