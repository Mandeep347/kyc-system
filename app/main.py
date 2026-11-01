from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api import actions, admin, alerts, customers, documents, exceptions, metrics, reviews
from app.core.config import settings
from app.core.database import get_db
from app.services.workflows import WorkflowError

app = FastAPI(
    title="KYC Evidence Tracker",
    version="0.6.0",
    description="Tracks onboarding evidence, validates completeness, manages periodic "
                "reviews and expiries. All data is synthetic. Write endpoints need the "
                "X-Role and X-User headers (demo identity).",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(WorkflowError)
async def workflow_error_handler(request: Request, exc: WorkflowError):
    """Business-rule failures (404/409/422) raised by app/services/workflows.py."""
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


for module in (customers, documents, alerts, reviews, exceptions, metrics, actions, admin):
    app.include_router(module.router)


@app.get("/health", tags=["system"])
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok", "as_of_date": settings.as_of_date}