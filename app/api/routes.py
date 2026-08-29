"""HTTP routes."""

from fastapi import APIRouter

from app.config import Settings, get_settings

router = APIRouter()


def _session_status(settings: Settings) -> dict[str, object]:
    """Describe the auth path without ever revealing a credential value."""
    if settings.li_at:
        source = "env_cookie"
    elif settings.li_username and settings.li_password:
        source = "programmatic_login"
    else:
        source = "unconfigured"
    return {"source": source, "checkpoint_blocking": False}


@router.get("/health")
async def health() -> dict[str, object]:
    return {"status": "ok", "session": _session_status(get_settings())}
