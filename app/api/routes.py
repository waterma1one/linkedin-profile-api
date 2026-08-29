"""HTTP routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import enforce_inbound_limit, require_api_key
from app.config import Settings, get_settings
from app.models import ProfileResponse

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
    settings = get_settings()
    return {
        "status": "ok",
        "session": _session_status(settings),
        # The public page is the production source; Voyager is opt-in. See design.md 8d.
        "data_source": "public_jsonld",
        "voyager_enabled": settings.voyager_enabled,
    }


@router.get(
    "/api/v1/profile",
    response_model=ProfileResponse,
    dependencies=[Depends(require_api_key), Depends(enforce_inbound_limit)],
)
async def profile(
    request: Request,
    url: Annotated[str, Query(description="A LinkedIn profile URL")],
) -> ProfileResponse:
    service = request.app.state.profile_service
    return await service.fetch(url)  # type: ignore[no-any-return]
