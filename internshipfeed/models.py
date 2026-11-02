"""Validated shared contracts for ingestion, storage, and API responses."""
from datetime import date, datetime
from typing import List, Literal, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
