// Format actual persisted ingestion completion time independently of browser refreshes.
export function syncLabel(timestamp: string | null): string {
  if (!timestamp) return 'No successful source sync yet.';
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return 'Sync time unavailable.';
  return `Last updated ${date.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}`;
}
