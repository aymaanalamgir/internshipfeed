export interface ProfileValues {
  target_roles: string[];
  skills: string[];
  preferred_locations: string[];
  preferred_terms: string[];
  graduation_year: number | null;
}

export interface CandidateProfile extends ProfileValues {
  id: number;
  created_at: string;
  updated_at: string;
}

export type TagField = 'target_roles' | 'skills' | 'preferred_locations' | 'preferred_terms';
export type PendingTags = Record<TagField, string>;
export type ProfileDraft = Omit<ProfileValues, 'graduation_year'> & { graduation_year: string };

export interface ResumeMetadata {
  id: number;
  candidate_profile_id: number;
  filename: string;
  uploaded_at: string;
}
