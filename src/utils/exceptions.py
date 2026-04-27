"""Domain-specific exceptions.

Catch the broadest base class (`JobApplierError`) at orchestrator-level to
ensure one bad company can never crash the whole 24h run.
"""

from __future__ import annotations


class JobApplierError(Exception):
    """Base for every error this system raises."""


class ConfigError(JobApplierError):
    """Invalid or missing configuration."""


class BrainError(JobApplierError):
    """Gemini API failure or unexpected model output."""


class ActorError(JobApplierError):
    """Browser/OpenClaw action failed."""


class SourcingError(JobApplierError):
    """Search/sourcing pipeline failure."""


class PortalNavigationError(ActorError):
    """We could not reach or interpret a careers portal."""


class FormFillError(ActorError):
    """We failed to fill a specific form field."""


class CaptchaEncountered(ActorError):
    """A captcha or bot-protection wall blocked the application."""


class RateLimitExceeded(JobApplierError):
    """We exceeded a self-imposed or remote rate limit."""
