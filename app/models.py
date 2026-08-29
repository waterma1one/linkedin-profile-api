"""Public response schema. Every field is optional because LinkedIn omits or gates
arbitrary sections, and an absent field is not an error."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Completeness = Literal["full", "partial", "unavailable"]


class LinkedInDate(BaseModel):
    year: int | None = None
    month: int | None = None
    day: int | None = None

    @property
    def iso(self) -> str | None:
        """Render at the precision LinkedIn actually supplied, never more."""
        if self.year is None:
            return None
        if self.month is None:
            return f"{self.year:04d}"
        if self.day is None:
            return f"{self.year:04d}-{self.month:02d}"
        return f"{self.year:04d}-{self.month:02d}-{self.day:02d}"


class Image(BaseModel):
    url: str
    width: int | None = None
    height: int | None = None
    expires_at: datetime | None = None


class ImageSet(BaseModel):
    profile: list[Image] = Field(default_factory=list)
    background: list[Image] = Field(default_factory=list)


class Location(BaseModel):
    full: str | None = None
    country: str | None = None
    city: str | None = None


class Company(BaseModel):
    name: str | None = None
    urn: str | None = None
    linkedin_url: str | None = None
    logo: str | None = None


class School(BaseModel):
    name: str | None = None
    urn: str | None = None
    linkedin_url: str | None = None
    logo: str | None = None


class Position(BaseModel):
    title: str | None = None
    employment_type: str | None = None
    company: Company = Field(default_factory=Company)
    location: str | None = None
    description: str | None = None
    start_date: LinkedInDate | None = None
    end_date: LinkedInDate | None = None
    is_current: bool = False
    duration_months: int | None = None
    group_id: str | None = None


class Education(BaseModel):
    school: School = Field(default_factory=School)
    degree: str | None = None
    field_of_study: str | None = None
    grade: str | None = None
    activities: str | None = None
    description: str | None = None
    start_date: LinkedInDate | None = None
    end_date: LinkedInDate | None = None


class Skill(BaseModel):
    name: str | None = None
    endorsement_count: int | None = None


class Certification(BaseModel):
    name: str | None = None
    issuer: str | None = None
    issue_date: LinkedInDate | None = None
    expiration_date: LinkedInDate | None = None
    credential_id: str | None = None
    credential_url: str | None = None


class Language(BaseModel):
    name: str | None = None
    proficiency: str | None = None


class Profile(BaseModel):
    urn: str | None = None
    public_identifier: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    full_name: str | None = None
    headline: str | None = None
    about: str | None = None
    location: Location = Field(default_factory=Location)
    industry: str | None = None
    pronouns: str | None = None
    follower_count: int | None = None
    connection_count: int | None = None
    # LinkedIn reports 500 for anyone with 500 or more, so the raw number would lie.
    connection_count_capped: bool = False
    is_premium: bool = False
    is_influencer: bool = False
    is_open_to_work: bool = False
    images: ImageSet = Field(default_factory=ImageSet)


class SectionWarning(BaseModel):
    section: str
    reason: str
    detail: str | None = None


class Meta(BaseModel):
    requested_url: str | None = None
    public_identifier: str | None = None
    fetched_at: datetime | None = None
    data_source: str | None = None
    cache_hit: bool = False
    duration_ms: int | None = None
    completeness: dict[str, Completeness] = Field(default_factory=dict)
    warnings: list[SectionWarning] = Field(default_factory=list)


class ProfileResponse(BaseModel):
    meta: Meta = Field(default_factory=Meta)
    profile: Profile = Field(default_factory=Profile)
    experience: list[Position] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    skills: list[Skill] = Field(default_factory=list)
    certifications: list[Certification] = Field(default_factory=list)
    languages: list[Language] = Field(default_factory=list)
    # Best-effort sections: populated when present, never scored for completeness.
    honors: list[dict] = Field(default_factory=list)
    publications: list[dict] = Field(default_factory=list)
    projects: list[dict] = Field(default_factory=list)
    volunteer: list[dict] = Field(default_factory=list)
