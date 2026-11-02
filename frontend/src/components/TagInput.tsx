import { useState } from 'react';
import { addTag, removeTag } from '../lib/form';
import { SkillTag } from './SkillTag';

interface Props {
  id: string;
  label: string;
  placeholder: string;
  values: string[];
  input: string;
  onChange: (values: string[]) => void;
  onInput: (value: string) => void;
  disabled: boolean;
}

export function TagInput({ id, label, placeholder, values, input, onChange, onInput, disabled }: Props) {
  const [announcement, setAnnouncement] = useState('');

  function commit() {
    const value = input.trim();
    if (!value) return;
    if (values.includes(value)) setAnnouncement(`${value} is already added.`);
    else {
      onChange(addTag(values, value));
      setAnnouncement(`${value} added.`);
    }
    onInput('');
  }

  return (
    <div className="profile-field">
      <label htmlFor={id}>{label}</label>
      <div className="tag-control">
        {values.length > 0 && (
          <ul className="tag-list" aria-label={`${label} values`}>
            {values.map((value, index) => (
              <li className="tag" key={value}><SkillTag label={value} disabled={disabled}
                removeLabel={`Remove ${value} from ${label.toLowerCase()}`}
                onRemove={() => { onChange(removeTag(values, index)); setAnnouncement(`${value} removed.`); }} /></li>
            ))}
          </ul>
        )}
        <div className="tag-entry">
          <input id={id} value={input} disabled={disabled} placeholder={placeholder} autoComplete="off"
            aria-describedby="tag-instructions" onChange={event => { onInput(event.target.value); setAnnouncement(''); }}
            onKeyDown={event => {
              if (event.key === 'Enter' && !event.nativeEvent.isComposing) { event.preventDefault(); commit(); }
            }} />
          <span className="enter-key" aria-hidden="true">↵</span>
        </div>
      </div>
      <span className="sr-only" role="status">{announcement}</span>
    </div>
  );
}
