export interface CanonicalJob {
  id: number;
  company: string;
  title: string;
  location: string;
  application_url: string | null;
  posted_at: string | null;
  status: 'open' | 'unknown' | 'closed' | 'dead';
}

export interface Recommendation {
  job: CanonicalJob;
  score: number;
  role_score: number;
  skills_score: number;
  location_score: number;
  term_score: number;
  matched_skills: string[];
  reasons: string[];
}

export type InteractionType = 'click' | 'save' | 'dismiss' | 'applied';

export interface InteractionEvent {
  id: number;
  candidate_profile_id: number;
  canonical_job_id: number;
  interaction_type: InteractionType;
  created_at: string;
}

export interface InteractionState {
  job_id: number;
  saved: boolean;
  dismissed: boolean;
  applied: boolean;
  last_interaction_at: string | null;
}

export type InteractionStates = Record<number, InteractionState>;
