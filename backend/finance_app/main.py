import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .accounting import router as accounting_router
from .assets import router as assets_router
from .auth import require_fresh_owner, require_owner
from .classification import router as classification_router
from .connections import router as connections_router
from .dashboard import router as dashboard_router
from .early_retirement import router as early_retirement_router
from .email_reviews import router as email_reviews_router
from .housing import HousingInputs, calculate_housing
from .household import router as household_router
from .investment_imports import router as investment_imports_router
from .investments import router as investments_router
from .mail_provider import create_mail_provider
from .lifecycle import router as lifecycle_router
from .mcp_finance import router as mcp_finance_router
from .operations import router as operations_router
from .retirement import RetirementInputs, calculate_retirement
from .retirement_optimizer import router as retirement_optimizer_router
from .retirement_uncertainty import router as retirement_uncertainty_router
from .recurring import router as recurring_router
from .plaid_provider import create_plaid_provider
from .payroll import router as payroll_router
from .scenarios import router as scenarios_router
from .storage import EncryptedStorage
from .sync import router as sync_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    data_dir = Path(os.environ.get("FINANCE_DATA_DIR", "/data"))
    encryption_key = os.environ.get("FINANCE_ENCRYPTION_KEY")
    if not encryption_key:
        raise RuntimeError("FINANCE_ENCRYPTION_KEY is required")

    storage = EncryptedStorage(data_dir, encryption_key)
    storage.initialize()
    app.state.storage = storage
    app.state.plaid = create_plaid_provider()
    app.state.mail = create_mail_provider()
    yield


app = FastAPI(
    title="Finance",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.include_router(scenarios_router)
app.include_router(connections_router)
app.include_router(dashboard_router)
app.include_router(sync_router)
app.include_router(payroll_router)
app.include_router(accounting_router)
app.include_router(assets_router)
app.include_router(classification_router)
app.include_router(investments_router)
app.include_router(investment_imports_router)
app.include_router(lifecycle_router)
app.include_router(mcp_finance_router)
app.include_router(operations_router)
app.include_router(household_router)
app.include_router(recurring_router)
app.include_router(retirement_optimizer_router)
app.include_router(retirement_uncertainty_router)
app.include_router(early_retirement_router)
app.include_router(email_reviews_router)

frontend_root = Path(__file__).resolve().parents[2] / "frontend"
frontend_dir = frontend_root / "dist" if (frontend_root / "dist").exists() else frontend_root
assets_dir = frontend_dir / "assets"
if assets_dir.exists():
    app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")


@app.get("/health")
def health() -> dict[str, str]:
    return {"service": "finance", "status": "ok"}


@app.get("/ready")
def ready(request: Request) -> dict[str, str]:
    return {
        "service": "finance",
        "status": "ready",
        "storage": "ready",
        "installation_fingerprint": request.app.state.storage.installation_fingerprint,
    }


@app.post("/api/public/housing/calculate")
def housing_calculation(inputs: HousingInputs) -> dict:
    return calculate_housing(inputs)


@app.post("/api/public/retirement/calculate")
def retirement_calculation(inputs: RetirementInputs) -> dict:
    return calculate_retirement(inputs)


@app.post("/api/private/security/fresh-check")
def fresh_authentication_check(request: Request) -> dict[str, bool]:
    require_fresh_owner(request)
    return {"fresh": True}


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(frontend_dir / "index.html", media_type="text/html")


@app.get("/housing", include_in_schema=False)
@app.get("/retirement", include_in_schema=False)
@app.get("/preview", include_in_schema=False)
def public_application_route() -> FileResponse:
    return FileResponse(frontend_dir / "index.html", media_type="text/html")


@app.get("/dashboard", include_in_schema=False)
@app.get("/scenarios", include_in_schema=False)
@app.get("/settings/connections", include_in_schema=False)
def private_application_route(request: Request) -> FileResponse:
    require_owner(request)
    return FileResponse(frontend_dir / "index.html", media_type="text/html")
