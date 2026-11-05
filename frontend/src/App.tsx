// Compose the current job view, shared action state, and profile/job drawers.
import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ApplyConfirmation } from './components/ApplyConfirmation';
import { TopNav } from './components/TopNav';
import { ProfileDrawer } from './components/ProfileDrawer';
import { JobDetailsDrawer } from './components/JobDetailsDrawer';
import { JobCollectionPage } from './pages/JobCollectionPage';
import { ListingsPage } from './pages/ListingsPage';
import { useJobActions } from './hooks/useJobActions';
import { invalidateJobs, queryKeys, sourcesQuery } from './lib/queries';
import { viewForPath } from './lib/navigation';
import { emptyFilters } from './lib/listings';

export default function App() {
  const [path, setPath] = useState(window.location.pathname);
  const view = viewForPath(path);
  const [profileOpen, setProfileOpen] = useState(window.location.pathname.replace(/\/$/, '') === '/profile');
  const [jobId, setJobId] = useState<number | null>(null);
  const [listingFilters, setListingFilters] = useState(emptyFilters);
  const client = useQueryClient();
  const { data: sources = [] } = useQuery(sourcesQuery());
  const workflow = useJobActions();
  useEffect(() => {
    const pop = () => { setPath(window.location.pathname); setJobId(null); setProfileOpen(window.location.pathname.replace(/\/$/, '') === '/profile'); };
    window.addEventListener('popstate', pop);
    return () => window.removeEventListener('popstate', pop);
  }, []);
  function closeProfile() {
    setProfileOpen(false);
    if (path.replace(/\/$/, '') === '/profile') { window.history.replaceState(null, '', '/'); setPath('/'); }
  }
  function navigate(href: string) {
    if (href !== path) window.history.pushState(null, '', href);
    setPath(href); setProfileOpen(false); setJobId(null);
    window.scrollTo(0, 0);
    requestAnimationFrame(() => document.querySelector<HTMLElement>('h1')?.focus());
  }
  const refresh = () => { void invalidateJobs(client); void client.invalidateQueries({ queryKey: queryKeys.sources }); };
  return <><a className="skip-link" href="#main-content">Skip to main content</a>
    <TopNav current={view} profileOpen={profileOpen} onProfile={() => setProfileOpen(true)} onNavigate={navigate} />
    {view === 'listings' ? <ListingsPage workflow={workflow} sources={sources} filters={listingFilters} onFiltersChange={setListingFilters} onUpdated={refresh} onOpenJob={setJobId} />
      : <JobCollectionPage key={view} view={view} workflow={workflow} onOpenJob={setJobId} onOpenProfile={() => setProfileOpen(true)} />}
    <p className="sr-only" role="status">{workflow.announcement}</p>
    {profileOpen && <ProfileDrawer onClose={closeProfile} />}
    {jobId !== null && <JobDetailsDrawer key={`job-${jobId}`} id={jobId} onClose={() => setJobId(null)} workflow={workflow} sources={sources} view={view} />}
    {workflow.application && <ApplyConfirmation key={`apply-${workflow.application.id}`} workflow={workflow} />}
  </>;
}
