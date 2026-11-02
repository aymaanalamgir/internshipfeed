import { useEffect, useState } from 'react';
import { getProfile } from '../api/profile';
import type { CandidateProfile } from '../types/profile';
import { Drawer } from './Drawer';
import { ProfileForm } from './ProfileForm';
import { ResumeUpload } from './ResumeUpload';

export function ProfileDrawer({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [profile, setProfile] = useState<CandidateProfile | null>(null);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setError(false);
    getProfile(controller.signal).then(value => { if (!controller.signal.aborted) setProfile(value); })
      .catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => controller.abort();
  }, [attempt]);
  return <Drawer title="Profile" kind="profile" onClose={onClose}>
    {error ? <div className="load-error" role="alert"><p>Could not load profile.</p><button className="button" onClick={() => setAttempt(value => value + 1)}>Try again</button></div>
      : !profile ? <div className="loading-profile" role="status">Loading profile…<div className="skeleton-field" /></div>
      : <ProfileForm profile={profile} onSaved={onSaved}><ResumeUpload /></ProfileForm>}
  </Drawer>;
}
