import { Icon } from './Icon';
import type { JobView } from '../types/jobs';
export function TopNav({ current, profileOpen, onProfile }: { current: JobView | 'listings'; profileOpen: boolean; onProfile: () => void }) {
  return <header className="site-header"><nav className="top-nav" aria-label="Main navigation">
    <a className="wordmark" href="/" aria-label="InternshipFeed home"><svg className="brand-mark" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M5 5h14v14H5zM2 9h7M2 15h7M15 2v7M15 15v7" stroke="currentColor" strokeWidth="1.5" /></svg>InternshipFeed<span>.</span></a>
    <div className="nav-links">{([['recommended', '/', 'Recommended'], ['listings', '/listings', 'All listings'], ['saved', '/saved', 'Saved'], ['applied', '/applied', 'Applied']] as const)
      .map(([view, href, label]) => <a key={view} href={href} aria-current={current === view ? 'page' : undefined}>{label}</a>)}</div>
    <button className="profile-trigger text-button" onClick={onProfile} aria-haspopup="dialog" aria-expanded={profileOpen}><Icon name="user" />Profile</button>
  </nav></header>;
}
