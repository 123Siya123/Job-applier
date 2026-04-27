"""Global settings loader.

Reads `.env` (via pydantic-settings) plus the JSON profile and search-parameter
files referenced by `PROFILE_PATH` / `SEARCH_PARAMS_PATH` env vars (with sane
defaults). Validated, typed and exposed as one immutable `Settings` instance
through `get_settings()`.
"""

from __future__ import annotations

import json
from enum import Enum
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class ActorBackend(str, Enum):
    PLAYWRIGHT = "playwright"
    OPENCLAW = "openclaw"


class GeoScope(str, Enum):
    PRIMARY = "PRIMARY"
    DE = "DE"
    EU = "EU"
    GLOBAL = "GLOBAL"


# ---------------------------------------------------------------------------
# Profile model (config/profile.json)
# ---------------------------------------------------------------------------

class Address(BaseModel):
    street: str
    postal_code: str
    city: str
    country: str


class Language(BaseModel):
    language: str
    level: str


class Personal(BaseModel):
    first_name: str
    last_name: str
    email: str
    phone: str
    address: Address
    date_of_birth: str | None = None
    nationality: str | None = None
    linkedin: str | None = None
    github: str | None = None
    languages: list[Language] = Field(default_factory=list)


class EducationEntry(BaseModel):
    degree: str
    institution: str
    start: str
    end: str | None = None
    gpa: str | None = None


class ExperienceEntry(BaseModel):
    title: str
    company: str
    start: str
    end: str | None = None
    description: str | None = None


class Skills(BaseModel):
    hard: list[str] = Field(default_factory=list)
    soft: list[str] = Field(default_factory=list)


class ProfileFiles(BaseModel):
    cv_path: str
    cover_letter_template_path: str | None = None
    transcript_path: str | None = None
    certificates_dir: str | None = None


class Preferences(BaseModel):
    earliest_start: str | None = None
    min_weekly_hours: int | None = None
    max_weekly_hours: int | None = None
    remote_ok: bool = True
    willing_to_relocate: bool = False


class Profile(BaseModel):
    personal: Personal
    education: list[EducationEntry] = Field(default_factory=list)
    experience: list[ExperienceEntry] = Field(default_factory=list)
    skills: Skills = Skills()
    files: ProfileFiles
    self_description: str
    preferences: Preferences = Preferences()


# ---------------------------------------------------------------------------
# Search parameters (config/search_params.json)
# ---------------------------------------------------------------------------

class PrimaryLocation(BaseModel):
    city: str
    radius_km: int = 50


class RelaxationStage(BaseModel):
    name: str
    trigger: str
    expand_radius_km: int | None = None
    geo_scope: GeoScope | None = None
    extra_job_types: list[str] = Field(default_factory=list)


class RelaxationStrategy(BaseModel):
    enabled: bool = True
    stages: list[RelaxationStage] = Field(default_factory=list)


class SearchKeywords(BaseModel):
    abstract_queries: list[str] = Field(default_factory=list)
    platform_queries: list[str] = Field(default_factory=list)


class SearchParams(BaseModel):
    job_types: list[str]
    fields: list[str]
    primary_location: PrimaryLocation
    relaxation_strategy: RelaxationStrategy = RelaxationStrategy()
    search_keywords: SearchKeywords = SearchKeywords()
    neighbouring_cities_seed: list[str] = Field(default_factory=list)
    target_company_count: int = 50
    blacklist_companies: list[str] = Field(default_factory=list)
    blacklist_domains: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Environment-driven settings (.env)
# ---------------------------------------------------------------------------

class Settings(BaseSettings):
    """Process-wide configuration.

    All values come from `.env` plus two JSON files. Loaded once at startup
    via `get_settings()`.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- file paths to JSON config ---
    profile_path: Path = Path("config/profile.json")
    search_params_path: Path = Path("config/search_params.json")

    # --- Brain ---
    gemini_api_key: str = "missing-key"
    gemini_model: str = "gemini-3.1-pro"
    gemini_vision_model: str = "gemini-3.1-pro"

    # --- Actor ---
    actor_backend: ActorBackend = ActorBackend.PLAYWRIGHT

    # --- Browser ---
    browser_headed: bool = True
    browser_channel: str = "chromium"
    browser_slow_mo_ms: int = 120

    # --- OpenClaw ---
    openclaw_host: str = "127.0.0.1"
    openclaw_port: int = 4711
    openclaw_timeout_seconds: int = 60

    # --- Email ---
    email_imap_host: str = "imap.gmail.com"
    email_imap_port: int = 993
    email_imap_user: str = ""
    email_imap_password: str = ""
    email_poll_interval_minutes: int = 120

    # --- DB ---
    database_url: str = "sqlite+aiosqlite:///./data/applier.sqlite3"

    # --- Dashboard ---
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8765

    # --- Orchestrator ---
    target_company_count: int = 50
    application_daily_limit: int = 200
    sourcing_daily_limit: int = 300
    global_rate_limit_rps: float = 2.0

    # --- Logging ---
    log_level: str = "INFO"
    log_file: Path = Path("./data/applier.log")

    # --- Lazy-loaded JSON config (filled by get_settings) ---
    _profile: Profile | None = None
    _search_params: SearchParams | None = None

    def load_profile(self) -> Profile:
        if self._profile is None:
            self._profile = _load_json_model(self.profile_path, Profile)
        return self._profile

    def load_search_params(self) -> SearchParams:
        if self._search_params is None:
            self._search_params = _load_json_model(self.search_params_path, SearchParams)
        return self._search_params


def _load_json_model(path: Path, model: type[BaseModel]):
    if not path.exists():
        raise FileNotFoundError(
            f"Required config file missing: {path} — copy the .example version and fill it in."
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"Invalid config in {path}:\n{exc}") from exc


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Singleton settings accessor."""
    return Settings()
