import { useEffect, useState } from 'react';
import { getSkillSuggestions } from '../api/resumes';
import { getProfile } from '../api/profile';
import type { CandidateProfile, ResumeSkillSuggestions } from '../types/profile';
import { Drawer } from './Drawer';
import { ProfileForm } from './ProfileForm';
import { ResumeUpload } from './ResumeUpload';

export function ProfileDrawer({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [resumeId, setResumeId] = useState<number | null>(null);
  const [suggestionData, setSuggestionData] = useState<ResumeSkillSuggestions | null>(null);
  const [suggestionLoading, setSuggestionLoading] = useState(false);
  const [suggestionError, setSuggestionError] = useState(false);
  const [suggestionAttempt, setSuggestionAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setSuggestionData(null); setSuggestionError(false); setSuggestionLoading(resumeId !== null);
    if (resumeId !== null) getSkillSuggestions(resumeId, controller.signal).then(value => {
      if (!controller.signal.aborted && value.resume_id === resumeId) setSuggestionData(value);
    }).catch(() => { if (!controller.signal.aborted) setSuggestionError(true); })
      .finally(() => { if (!controller.signal.aborted) setSuggestionLoading(false); });
    return () => controller.abort();
  }, [resumeId, suggestionAttempt]);
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
      : <ProfileForm profile={profile} onSaved={onSaved} suggestions={{
        data: suggestionData?.resume_id === resumeId ? suggestionData : null,
        loading: resumeId !== null && (suggestionLoading || (!suggestionError && suggestionData?.resume_id !== resumeId)),
        error: suggestionError, retry: () => setSuggestionAttempt(value => value + 1),
      }}><ResumeUpload onResumeChange={resume => setResumeId(resume?.id ?? null)} /></ProfileForm>}
  </Drawer>;
}
