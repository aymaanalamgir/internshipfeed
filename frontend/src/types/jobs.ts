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
  saved_at: string | null;
  applied_at: string | null;
  last_event_id: number;
}

export interface InteractionReceipt extends InteractionEvent {
  interaction_state: InteractionState;
}

export interface CandidateJob extends Recommendation {
  interaction_state: InteractionState;
}

export type JobView = 'recommended' | 'saved' | 'applied';
export type InteractionStates = Record<number, InteractionState>;
