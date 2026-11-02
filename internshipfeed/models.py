"""Validated shared contracts for ingestion, storage, and API responses."""
from datetime import date, datetime
from typing import List, Literal, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CandidateProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int = Field(gt=0)
    target_roles: List[str]
    skills: List[str]
    preferred_locations: List[str]
    preferred_terms: List[str]
    graduation_year: Optional[int]
    created_at: datetime
    updated_at: datetime


class CandidateProfileUpdate(BaseModel):
    """Complete editable state; even a nullable graduation year is required."""
    model_config = ConfigDict(extra="forbid")
    target_roles: List[str]
    skills: List[str]
    preferred_locations: List[str]
    preferred_terms: List[str]
    graduation_year: Optional[int] = Field(ge=1900, le=2200, strict=True)

    @field_validator("target_roles", "skills", "preferred_locations", "preferred_terms", mode="before")
    @classmethod
    def normalize_preferences(cls, value: object) -> List[str]:
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError("Expected a list of strings")
        # Exact, case-sensitive duplicates preserve the first spelling and order.
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))


class Resume(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int = Field(gt=0)
    candidate_profile_id: int = Field(gt=0)
    filename: str
    extracted_text: str
    uploaded_at: datetime


class JobKeywords(BaseModel):
    model_config = ConfigDict(extra="forbid")
    languages: List[str] = Field(default_factory=list)
    frameworks: List[str] = Field(default_factory=list)
    tools_and_platforms: List[str] = Field(default_factory=list)
    concepts: List[str] = Field(default_factory=list)
    requirements: List[str] = Field(default_factory=list)


class JobListing(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)
    id: Optional[int] = None
    canonical_job_id: Optional[int] = None
    source_id: Optional[int] = None
    company: str = Field(min_length=1)
    title: str = Field(min_length=1)
    location: str = Field(min_length=1)
    url: Optional[str] = None
    last_known_url: Optional[str] = None
    source: str = Field(min_length=1)
    external_id: str = Field(min_length=1)
    link_checked_at: Optional[datetime] = None
    posted_at: Optional[date] = None
    term: Optional[str] = None
    status: Literal["open", "closed", "unknown", "dead"]
    description: Optional[str] = None
    keywords: JobKeywords = Field(default_factory=JobKeywords)
    enrichment_status: Literal["not_requested", "not_attempted", "success", "unavailable", "skipped", "failed"] = "not_requested"
    enrichment_error: Optional[str] = None
    extraction_method: Optional[str] = None
    resolved_url: Optional[str] = None
    enriched_at: Optional[datetime] = None
    raw_company: Optional[str] = None
    raw_title: Optional[str] = None
    raw_location: Optional[str] = None
    raw_url: Optional[str] = None
    raw_posted_at: Optional[str] = None
    raw_row: Optional[str] = None
    snapshot_id: str
    source_line: int = Field(ge=1)
    first_seen_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None

    @field_validator("company", "title", "location", "source", "external_id")
    @classmethod
    def nonblank(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Field must not be blank")
        return value

    @field_validator("url", "last_known_url", "resolved_url")
    @classmethod
    def http_url(cls, value):
        if value is not None:
            parsed = urlparse(value)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError("Expected an absolute HTTP(S) URL")
        return value


class SourceJobReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    source: str
    source_id: int
    external_id: str
    match_method: str
    match_confidence: float
    matched_url: Optional[str]
    company: str
    title: str
    location: str
    url: Optional[str]
    last_known_url: Optional[str]
    posted_at: Optional[date]
    term: Optional[str]
    status: Literal["open", "closed", "unknown", "dead"]
    snapshot_id: str
    source_line: int
    first_seen_at: datetime
    last_seen_at: datetime


class CanonicalJob(BaseModel):
    """The canonical opportunity contract; source/enrichment data lives separately."""
    model_config = ConfigDict(extra="forbid")
    id: int = Field(gt=0)
    company: str = Field(min_length=1)
    title: str = Field(min_length=1)
    location: str = Field(min_length=1)
    application_url: Optional[str]
    posted_at: Optional[date]
    status: Literal["open", "closed", "unknown", "dead"]

    @field_validator("application_url")
    @classmethod
    def http_url(cls, value):
        return JobListing.http_url(value)


class JobDetails(BaseModel):
    """Associated source and enrichment context, outside the CanonicalJob entity."""
    model_config = ConfigDict(extra="forbid")
    job: CanonicalJob
    term: Optional[str]
    description: Optional[str]
    keywords: JobKeywords
    sources: List[SourceJobReference]


class MatchResult(BaseModel):
    """On-request score with component values and the evidence behind them."""
    model_config = ConfigDict(extra="forbid")
    job: CanonicalJob
    score: int = Field(ge=0, le=100)
    role_score: int = Field(ge=0, le=40)
    skills_score: int = Field(ge=0, le=30)
    location_score: int = Field(ge=0, le=20)
    term_score: int = Field(ge=0, le=10)
    matched_skills: List[str]
    reasons: List[str]


class InteractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["click", "save", "dismiss", "applied"]


class JobInteraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int = Field(gt=0)
    candidate_profile_id: int = Field(gt=0)
    canonical_job_id: int = Field(gt=0)
    interaction_type: Literal["click", "save", "dismiss", "applied"]
    created_at: datetime


class JobInteractionState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: int = Field(gt=0)
    saved: bool
    dismissed: bool
    applied: bool
    last_interaction_at: datetime


class StoredCanonicalJob(CanonicalJob):
    """Persisted metadata; CanonicalJob remains the small public API contract."""
    description: Optional[str]
    keywords: JobKeywords
    first_seen_at: datetime
    last_seen_at: datetime
    created_at: datetime
    updated_at: datetime
