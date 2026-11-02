"""Source-neutral intermediate records emitted by repository parsers."""
from typing import Literal, Optional
from pydantic import AliasChoices, BaseModel, Field


class SourceJobRecord(BaseModel):
    company: str
    title: str = Field(validation_alias=AliasChoices("title", "role"))
    location: str
    url: Optional[str] = Field(default=None, validation_alias=AliasChoices("url", "raw_url"))
    status: Literal["open", "closed", "unknown"]
    posted_at_raw: str = Field(default="", validation_alias=AliasChoices("posted_at_raw", "date_posted_raw"))
    date_kind: Literal["exact", "relative", "yearless", "missing"] = "exact"
    term: Optional[str] = None
    external_id: Optional[str] = None
    source_line: int = Field(ge=1)
    raw_company: Optional[str] = None
    raw_title: Optional[str] = None
    raw_location: Optional[str] = None
    raw_url: Optional[str] = None
    raw_posted_at: Optional[str] = None
    raw_row: str
