import { useEffect, useState } from 'react';
import type { CandidateProfile } from '../types/profile';
import { getProfile } from '../api/profile';
import { ProfileForm } from '../components/ProfileForm';
import { ResumeUpload } from '../components/ResumeUpload';

export function ProfilePage() {
  const [profile, setProfile] = useState<CandidateProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(false);
    getProfile(controller.signal)
      .then(setProfile)
      .catch(() => { if (!controller.signal.aborted) setError(true); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [attempt]);

  return (
    <main className="profile-page" id="main-content">
      <div className="page-heading"><h1>Profile</h1><span className="workspace-label"><span />Candidate workspace</span></div>
      <div className="profile-layout">
        <aside className="margin-note"><span className="note-index">01 / PREFERENCES</span><p>Used to rank<br />internship matches.</p></aside>
        <div className="profile-content">
          {loading ? <div className="loading-profile" role="status" aria-label="Loading profile" aria-busy="true">
            <span>Loading profile…</span>{[0, 1, 2, 3].map(value => <div key={value} className="skeleton-field" />)}
          </div> : error || !profile ? <div className="load-error" role="alert">
            <p>Could not load profile.</p><button className="button button-secondary" onClick={() => setAttempt(value => value + 1)}>Try again</button>
          </div> : <><ProfileForm profile={profile} /><ResumeUpload /></>}
          <footer className="profile-footer"><span>InternshipFeed</span><span>Candidate profile</span></footer>
        </div>
      </div>
    </main>
  );
}
