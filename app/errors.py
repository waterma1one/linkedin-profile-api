"""Exception hierarchy shared across the LinkedIn client and API layers."""


class LinkedInError(Exception):
    """Base class for every error raised by this service."""

    code = "internal_error"
    http_status = 500
    hint: str | None = None


class InvalidProfileURL(LinkedInError):
    code = "invalid_url"
    http_status = 400
    hint = "Provide a URL of the form https://www.linkedin.com/in/<slug>"


class SessionUnavailable(LinkedInError):
    code = "session_unavailable"
    http_status = 503
    hint = "No usable LinkedIn session. Check /health for the active auth path."


class CheckpointRequired(SessionUnavailable):
    code = "checkpoint_required"
    hint = "LinkedIn issued a CAPTCHA challenge. Log in manually and supply a fresh LI_AT."

    def __init__(self, message: str, challenge_url: str | None = None) -> None:
        super().__init__(message)
        self.challenge_url = challenge_url


class BadCredentials(SessionUnavailable):
    code = "bad_credentials"
    hint = "LI_USERNAME or LI_PASSWORD was rejected by LinkedIn."


class ProfileNotFound(LinkedInError):
    code = "profile_not_found"
    http_status = 404
    hint = "The profile does not exist or is not visible to the backing account."


class RateLimited(LinkedInError):
    code = "rate_limited"
    http_status = 429
    hint = "Slow down and retry after the interval in the Retry-After header."

    def __init__(self, message: str, retry_after: int = 60) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class BotDetected(LinkedInError):
    code = "bot_detected"
    http_status = 502
    hint = "LinkedIn returned HTTP 999. The backing account may be flagged."


class UpstreamError(LinkedInError):
    code = "upstream_error"
    http_status = 502
    hint = "LinkedIn was unreachable or returned an unexpected response."
