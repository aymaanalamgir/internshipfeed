import { TopNav } from './components/TopNav';
import { ProfilePage } from './pages/ProfilePage';
import { RecommendationsPage } from './pages/RecommendationsPage';

export default function App() {
  const recommended = window.location.pathname.startsWith('/recommendations');
  return <><a className="skip-link" href="#main-content">Skip to main content</a>
    <TopNav current={recommended ? 'recommended' : 'profile'} />{recommended ? <RecommendationsPage /> : <ProfilePage />}</>;
}
