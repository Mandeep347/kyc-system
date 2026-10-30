from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db

app = FastAPI(title="KYC Evidence Tracker")


@app.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))  # fails loudly if DB is unreachable
    return {"status": "ok", "as_of_date": settings.as_of_date}