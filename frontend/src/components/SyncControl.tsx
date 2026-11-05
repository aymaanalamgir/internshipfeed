// Show persisted ingestion freshness and allow source sync only when the API authorizes it.
import { useEffect, useRef, useState } from 'react';
import { getIngestionStatus, updateListings } from '../api/jobs';
import type { IngestionStatus, SyncRun } from '../types/jobs';
import { syncLabel } from '../lib/ingestion';
import { Icon } from './Icon';

export function SyncControl({ onUpdated }: { onUpdated: () => void }) {
  const [status, setStatus] = useState<IngestionStatus | null>(null);
  const [run, setRun] = useState<SyncRun | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState('');
  const updated = useRef(onUpdated);
  updated.current = onUpdated;
  const running = run?.status === 'running';
  useEffect(() => {
    const controller = new AbortController();
    function refresh() {
      getIngestionStatus(controller.signal).then(value => {
        if (controller.signal.aborted) return;
        setStatus(value); setRun(value.latest_run); setError('');
        if (running && value.latest_run?.status !== 'running') updated.current();
      }).catch(() => { if (!controller.signal.aborted) setError('Sync status unavailable.'); });
    }
    refresh();
    const timer = window.setInterval(refresh, running ? 2000 : 60000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [running]);
  async function start() {
    setStarting(true); setError('');
    try { setRun(await updateListings()); }
    catch { setError('Could not update listings. Please try again.'); }
    finally { setStarting(false); }
  }
  return <div className="sync-control">
    {status?.manual_sync_allowed && <button id="sync-jobs" className="text-button" disabled={starting || running} onClick={() => void start()}>
      <Icon name="refresh" />{starting || running ? 'Updating…' : 'Update listings'}</button>}
    <span className="sync-status" id="sync-status" role="status">{error || (running ? 'Updating listings…' : status ? syncLabel(status.last_successful_at) : 'Loading sync status…')}</span>
    {status && !status.manual_sync_allowed && <span className="sync-status">{status.scheduled_sync_configured ? 'Automatic daily source sync' : 'Automatic sync needs server configuration.'}</span>}
  </div>;
}
