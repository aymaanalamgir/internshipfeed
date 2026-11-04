// Keep initial loading and nonblocking refresh feedback consistent across views.
export function LoadingState({ label = 'Loading jobs…' }: { label?: string }) {
  return <div className="loading-state" role="status"><span className="spinner" aria-hidden="true" />{label}</div>;
}

export function RefreshState({ fetching, error, onRetry }: { fetching: boolean; error: boolean; onRetry: () => void }) {
  if (fetching) return <p className="refresh-state" role="status">Refreshing…</p>;
  if (error) return <p className="refresh-state" role="alert">Could not refresh. Showing previously loaded data.{' '}
    <button className="text-button" onClick={onRetry}>Try again</button></p>;
  return null;
}
