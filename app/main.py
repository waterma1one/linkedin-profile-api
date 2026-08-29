"""Application factory."""

from fastapi import FastAPI

from app.api.routes import router


def create_app() -> FastAPI:
    app = FastAPI(
        title="LinkedIn Profile API",
        version="0.1.0",
        description="Returns a LinkedIn profile as structured JSON.",
    )
    app.include_router(router)
    return app


app = create_app()
