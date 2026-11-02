import { Icon } from './Icon';
export function SkillTag({ label, onRemove, removeLabel, disabled }: {
  label: string; onRemove?: () => void; removeLabel?: string; disabled?: boolean;
}) {
  return <span className="skill-tag"><span>{label}</span>{onRemove && <button type="button" disabled={disabled}
    aria-label={removeLabel ?? `Remove ${label}`} onClick={onRemove}><Icon name="close" /></button>}</span>;
}
