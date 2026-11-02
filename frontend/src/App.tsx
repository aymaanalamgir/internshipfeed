import { TopNav } from './components/TopNav';
import { ProfilePage } from './pages/ProfilePage';
import { JobCollectionPage } from './pages/JobCollectionPage';
import type { JobView } from './types/jobs';

export default function App() {
  const path = window.location.pathname;
  const view: JobView | 'profile' = path.startsWith('/recommendations') ? 'recommended'
    : path.startsWith('/saved') ? 'saved' : path.startsWith('/applied') ? 'applied' : 'profile';
  return <><a className="skip-link" href="#main-content">Skip to main content</a>
    <TopNav current={view} />{view === 'profile' ? <ProfilePage /> : <JobCollectionPage view={view} />}</>;
}
