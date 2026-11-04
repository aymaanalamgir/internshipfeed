// Show genuine upstream dates consistently without substituting discovery timestamps.
import { postedDateLabel } from '../lib/dates';

export function PostedDate({ date, showUnknown = false }: { date: string | null; showUnknown?: boolean }) {
  const label = postedDateLabel(date);
  if (!label) return showUnknown ? <p className="posted-date">Posting date unavailable</p> : null;
  return <p className="posted-date">Posted <time dateTime={date!} title={date!}>{label}</time></p>;
}
