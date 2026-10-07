"""Optional isolated Devspace relay using the gateway's existing review DB/auth."""
from types import SimpleNamespace

from fastapi import FastAPI

from ..api.review_auth import load_credentials
from ..api.review_training import router
from ..config import Settings
from ..state.review_store import ReviewStore


def create_training_gateway(settings):
    if not settings.review_enabled:
        raise ValueError('review auth must be enabled')
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.gateway = SimpleNamespace(settings=settings,
        review_credentials=load_credentials(settings.review_credentials_file),
        review_store=ReviewStore(settings.review_db_path))
    app.include_router(router, prefix='/review-api/reward')
    return app


app = create_training_gateway(Settings())
