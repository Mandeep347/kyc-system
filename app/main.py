from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api import alerts, customers, documents, exceptions, metrics, reviews
from app.core.config import settings
from app.core.database import get_db

app = FastAPI(
    title="KYC Evidence Tracker",
    version="0.5.0",
    description="Tracks onboarding evidence, validates completeness, manages periodic "
                "reviews and expiries. All data is synthetic.",
)

# lets the React/Vite frontend (Phase 7) call this API from the browser
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

for module in (customers, documents, alerts, reviews, exceptions, metrics):
    app.include_router(module.router)


@app.get("/health", tags=["system"])
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))  # fails loudly if the DB is unreachable
    return {"status": "ok", "as_of_date": settings.as_of_date}