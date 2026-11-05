// Describe job, ranking, source, ingestion, and candidate-interaction API contracts.
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

export type SaveAction = 'save' | 'unsave';
export type AppliedAction = 'applied' | 'unapplied';
export type DismissAction = 'dismiss' | 'undismiss';
export type InteractionType = 'click' | SaveAction | DismissAction | AppliedAction;

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
  dismissed_at: string | null;
  last_event_id: number;
}

export interface InteractionReceipt extends InteractionEvent {
  interaction_state: InteractionState;
}

export type ApplicationStatus = 'applied' | 'interview' | 'rejected' | 'ghosted' | 'offered';

export interface JobApplication {
  id: number;
  candidate_profile_id: number;
  canonical_job_id: number;
  status: ApplicationStatus;
  applied_at: string;
  status_updated_at: string;
  created_at: string;
  updated_at: string;
}

export interface CandidateJob extends Recommendation {
  application?: JobApplication;
  interaction_state: InteractionState;
  term: string | null;
  keywords: JobKeywords;
}

export interface RankingSignals {
  profile_score: number;
  behavior_score: number;
  final_score: number;
  behavior_reasons: string[];
}

export interface RankedRecommendation extends CandidateJob, RankingSignals {}

export type JobView = 'recommended' | 'saved' | 'applied' | 'dismissed';
export type InteractionStates = Record<number, InteractionState>;

export interface JobKeywords {
  languages: string[];
  frameworks: string[];
  tools_and_platforms: string[];
  concepts: string[];
  requirements: string[];
}
export interface SourceReference {
  company: string;
  source_id: number;
  source: string;
  title: string;
  location: string;
  term: string | null;
  status: CanonicalJob['status'];
}
export interface JobDetails {
  job: CanonicalJob;
  term: string | null;
  description: string | null;
  keywords: JobKeywords;
  sources: SourceReference[];
}
export interface JobListItem {
  job: CanonicalJob;
  term: string | null;
  skills: string[];
  interaction_state: InteractionState;
}
export interface JobListPage {
  items: JobListItem[];
  limit: number;
  total: number;
  has_more: boolean;
  next_cursor: string | null;
}
export interface ListingFilterOptions { locations: string[]; terms: string[]; }
export interface JobSource { id: number; name: string; url: string; }
export interface SyncRun { id: string; status: string; inserted?: number; updated?: number; processed?: number; scraped?: number; finished_at?: string | null; }
export interface IngestionStatus { latest_run: SyncRun | null; last_successful_at: string | null; manual_sync_allowed: boolean; scheduled_sync_configured: boolean; }
