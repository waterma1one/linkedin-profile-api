"""HTTP routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import enforce_inbound_limit, require_api_key
from app.models import ProfileResponse

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, object]:
    """Liveness and the source profiles are actually served from.

    Deliberately does not report session or credential state. The service answers from the
    logged-out public page and holds no LinkedIn session, so anything it said about cookies
    or login would describe a path it never takes. It also never returns a secret value.
    """
    return {"status": "ok", "data_source": "public_jsonld"}


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
