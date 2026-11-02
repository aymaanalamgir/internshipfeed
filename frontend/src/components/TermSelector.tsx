import { INTERNSHIP_TERMS, addTerm } from '../lib/profile-options';
import { SkillTag } from './SkillTag';
export function TermSelector({ values, onChange }: { values: string[]; onChange: (values: string[]) => void }) {
  return <div className="profile-field">
    <label htmlFor="preferred_terms">Preferred terms</label>
    <ul className="tag-list" aria-label="Preferred terms values">
      {values.map((value, index) => <li className="tag" key={value}><SkillTag label={value}
        removeLabel={`Remove ${value} from preferred terms`} onRemove={() => onChange(values.filter((_, position) => position !== index))} /></li>)}
    </ul>
    <select id="preferred_terms" value="" onChange={event => onChange(addTerm(values, event.target.value))}>
      <option value="">Select term</option>
      {INTERNSHIP_TERMS.map(term => <option key={term} disabled={values.some(value => value.trim().toLowerCase() === term.toLowerCase())}>{term}</option>)}
    </select>
  </div>;
}
