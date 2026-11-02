import { Icon } from './Icon';
export function SkillTag({ label, onRemove, onAdd, removeLabel, disabled }: {
  label: string; onRemove?: () => void; onAdd?: () => void; removeLabel?: string; disabled?: boolean;
}) {
  if (onAdd) return <button type="button" className="skill-tag skill-suggestion" disabled={disabled}
    aria-label={`Add ${label} to skills`} onClick={onAdd}><Icon name="plus" /><span>{label}</span></button>;
  return <span className="skill-tag"><span>{label}</span>{onRemove && <button type="button" disabled={disabled}
    aria-label={removeLabel ?? `Remove ${label}`} onClick={onRemove}><Icon name="close" /></button>}</span>;
}
