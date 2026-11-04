// Offer alias-filtered resume skills as optional additions to the profile draft.
import type { ResumeSkillSuggestions } from '../types/profile';
import { availableSkills } from '../lib/profile-options';
import { SkillTag } from './SkillTag';
import { LoadingState } from './LoadingState';
export interface SuggestionState {
  data: ResumeSkillSuggestions | null;
  loading: boolean;
  error: boolean;
  retry: () => void;
}
export function SkillSuggestions({ state, selected, onAdd, disabled }: {
  state: SuggestionState; selected: string[]; onAdd: (skills: string[]) => void; disabled: boolean;
}) {
  const skills = state.data ? availableSkills(state.data.skills, selected, state.data.aliases) : [];
  if (!state.loading && !state.error && !state.data) return null;
  return <section className="skill-suggestions" aria-label="Suggested from résumé" aria-busy={state.loading}>
    <div className="suggestion-heading"><span>Suggested from résumé</span>
      {skills.length > 1 && <button type="button" className="text-button" disabled={disabled} onClick={() => onAdd(skills)}>Add all</button>}</div>
    {state.loading ? <LoadingState label="Finding technical skills…" />
      : state.error ? <p role="status">Could not load suggestions. <button type="button" className="text-button" onClick={state.retry}>Try again</button></p>
        : skills.length ? <div className="skill-tags">{skills.map(skill => <SkillTag key={skill} label={skill} disabled={disabled} onAdd={() => onAdd([skill])} />)}</div>
          : <p role="status">{state.data?.skills.length ? 'Detected skills are already selected.' : 'No technical skills detected.'}</p>}
  </section>;
}
