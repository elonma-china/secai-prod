"""Read-only showcase endpoints for the nghiệm-thu demo (docs/nghiem-thu-showcase/).

Stage 1 groups: finetune (F), model_info (D), kpi_bench (K), sanitize_demo (L),
evidence (E). Each submodule exposes a module-level ``router: APIRouter`` that
``secai_gateway.api.routes.register_routes`` mounts onto the app — see that
function for the ``app.include_router(...)`` calls (kept there, not here, so
this package never needs to import ``FastAPI`` app/state).
"""
