from types import SimpleNamespace
from fastapi import FastAPI
from secai_gateway.report_v2.api import router
from secai_gateway.config import get_settings
from secai_gateway.api.middleware import configure_middleware
from secai_gateway.state import FixedWindowRateLimiter
app = FastAPI(title="Devspace Report API")
app.include_router(router)
configure_middleware(app, SimpleNamespace(settings=get_settings(), rate_limiter=FixedWindowRateLimiter()))
@app.get("/health")
async def health():
    return {"status": "ok", "service": "devspace-reports"}
