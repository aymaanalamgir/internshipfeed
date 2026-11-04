// Load the profile and latest resume suggestions while preserving the underlying view.
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { profileQuery, suggestionsQuery } from '../lib/queries';
import { LoadingState, RefreshState } from './LoadingState';
import { Drawer } from './Drawer';
import { ProfileForm } from './ProfileForm';
import { ResumeUpload } from './ResumeUpload';

export function ProfileDrawer({ onClose }: { onClose: () => void }) {
  const [resumeId, setResumeId] = useState<number | null>(null);
  const suggestions = useQuery(suggestionsQuery(resumeId));
  const query = useQuery(profileQuery());
  const profile = query.data;
  return <Drawer title="Profile" kind="profile" onClose={onClose}>
    {!profile && query.isError ? <div className="load-error" role="alert"><p>Could not load profile.</p><button className="button" onClick={() => void query.refetch()}>Try again</button></div>
      : !profile ? <LoadingState label="Loading profile…" />
      : <><RefreshState fetching={query.isFetching} error={query.isError} onRetry={() => void query.refetch()} /><ProfileForm profile={profile} suggestions={{
        data: suggestions.data?.resume_id === resumeId ? suggestions.data : null,
        loading: resumeId !== null && suggestions.isPending,
        error: suggestions.isError && !suggestions.data, retry: () => void suggestions.refetch(),
      }}><ResumeUpload onResumeChange={resume => setResumeId(resume?.id ?? null)} /></ProfileForm></>}
  </Drawer>;
}
