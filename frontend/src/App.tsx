import { TopNav } from './components/TopNav';
import { ProfilePage } from './pages/ProfilePage';

export default function App() {
  return <><a className="skip-link" href="#main-content">Skip to profile</a><TopNav /><ProfilePage /></>;
}
