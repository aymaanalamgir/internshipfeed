// Establish one browser session before mounting parallel candidate-data queries.
import { useEffect, useState, type ReactNode } from 'react';
import { ensureSession } from '../api/http';
import { LoadingState } from './LoadingState';

export function SessionGate({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);
  function start() {
    setFailed(false);
    void ensureSession().then(() => setReady(true)).catch(() => setFailed(true));
  }
  useEffect(start, []);
  if (ready) return children;
  return <main className="workspace">
    {failed ? <div className="load-error" role="alert"><p>Could not start your session.</p><button className="button" onClick={start}>Try again</button></div>
      : <LoadingState label="Starting your profile…" />}
  </main>;
}
