// Start ingestion, poll batch progress, and refresh listings after completion.
import { useEffect, useRef, useState } from 'react';
import { getLatestSync, updateListings } from '../api/jobs';
import type { SyncRun } from '../types/jobs';
import { Icon } from './Icon';

export function SyncControl({ onUpdated }: { onUpdated: () => void }) {
  const [run, setRun] = useState<SyncRun | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState('');
  const updated = useRef(onUpdated);
  updated.current = onUpdated;
  const running = run?.status === 'running';
  useEffect(() => {
    const controller = new AbortController();
    getLatestSync(controller.signal).then(value => { if (!controller.signal.aborted) setRun(value); }).catch(() => {});
    return () => controller.abort();
  }, []);
  useEffect(() => {
    if (!running) return;
    const controller = new AbortController();
    const timer = window.setInterval(() => {
      getLatestSync(controller.signal).then(value => {
        if (controller.signal.aborted || !value) return;
        setRun(value); setError('');
        if (value.status !== 'running') updated.current();
      }).catch(() => { if (!controller.signal.aborted) setError('Waiting for listing updates…'); });
    }, 2000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [running]);
  async function start() {
    setStarting(true); setError('');
    try { setRun(await updateListings()); }
    catch { setError('Could not update listings. Please try again.'); }
    finally { setStarting(false); }
  }
  return <div className="sync-control"><button id="sync-jobs" className="text-button" disabled={starting || running} onClick={() => void start()}>
    <Icon name="refresh" />{starting || running ? 'Updating…' : 'Update listings'}</button>
    <span className="sync-status" id="sync-status" role="status">{error || (running ? 'Updating listings…' : run ? run.status === 'success' ? 'Listings updated.' : 'Some listings could not be updated.' : '')}</span>
  </div>;
}
