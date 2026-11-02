export function TopNav({ current }: { current: 'profile' | 'recommended' }) {
  return (
    <header className="site-header">
      <nav className="top-nav" aria-label="Main navigation">
        <a className="wordmark" href="/feed">
          <svg className="brand-mark" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path d="M5 5h14v14H5zM2 9h7M2 15h7M15 2v7M15 15v7" stroke="currentColor" strokeWidth="1.5" />
          </svg>
          InternshipFeed<span className="brand-period">.</span>
        </a>
        <div className="nav-links">
          <a href="/feed">Feed</a>
          <a href="/recommendations" aria-current={current === 'recommended' ? 'page' : undefined}>Recommended</a>
          <a href="/profile" aria-current={current === 'profile' ? 'page' : undefined}>Profile</a>
        </div>
      </nav>
    </header>
  );
}
