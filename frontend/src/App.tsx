// Compose the current job view, shared action state, and profile/job drawers.
import { useEffect, useState } from 'react';
import { TopNav } from './components/TopNav';
import { ProfileDrawer } from './components/ProfileDrawer';
import { JobDetailsDrawer } from './components/JobDetailsDrawer';
import { JobCollectionPage } from './pages/JobCollectionPage';
import { ListingsPage } from './pages/ListingsPage';
import { useJobActions } from './hooks/useJobActions';
import { getSources } from './api/jobs';
import { viewForPath } from './lib/navigation';
import type { JobSource } from './types/jobs';

export default function App() {
  const view = viewForPath(window.location.pathname);
  const [profileOpen, setProfileOpen] = useState(window.location.pathname.replace(/\/$/, '') === '/profile');
  const [jobId, setJobId] = useState<number | null>(null);
  const [revision, setRevision] = useState(0);
  const [sources, setSources] = useState<JobSource[]>([]);
  const workflow = useJobActions();
  useEffect(() => {
    const controller = new AbortController();
    getSources(controller.signal).then(value => { if (!controller.signal.aborted) setSources(value); }).catch(() => {});
    return () => controller.abort();
  }, []);
  function closeProfile() {
    setProfileOpen(false);
    if (window.location.pathname.replace(/\/$/, '') === '/profile') window.history.replaceState(null, '', '/');
  }
  const refresh = () => setRevision(value => value + 1);
  return <><a className="skip-link" href="#main-content">Skip to main content</a>
    <TopNav current={view} profileOpen={profileOpen} onProfile={() => setProfileOpen(true)} />
    {view === 'listings' ? <ListingsPage workflow={workflow} sources={sources} revision={revision} onUpdated={refresh} onOpenJob={setJobId} />
      : <JobCollectionPage view={view} workflow={workflow} revision={revision} onOpenJob={setJobId} onOpenProfile={() => setProfileOpen(true)} />}
    <p className="sr-only" role="status">{workflow.announcement}</p>
    {profileOpen && <ProfileDrawer onClose={closeProfile} onSaved={refresh} />}
    {jobId !== null && <JobDetailsDrawer key={jobId} id={jobId} onClose={() => setJobId(null)} workflow={workflow} sources={sources} view={view} />}
  </>;
}
