from sqlalchemy import text

from app.core.database import Base, engine
import app.models  # noqa: F401  (registers the tables on Base.metadata)

# Parents first, children after. Foreign keys require this order.
LOAD_ORDER = [
    "customer_types", "kyc_requirements", "customers",
    "documents", "beneficial_owners", "risk_ratings", "review_cases",
    "verifications", "document_requirement_links", "alerts", "audit_events",
]


def load(tables: dict[str, list[dict]], chunk_size: int = 5000) -> None:
    # engine.begin() = ONE transaction: if anything fails, nothing is saved
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE " + ", ".join(LOAD_ORDER) + " CASCADE"))
        for name in LOAD_ORDER:
            rows = tables.get(name)
            if not rows:
                continue  # stages we haven't built yet
            table = Base.metadata.tables[name]
            for i in range(0, len(rows), chunk_size):
                conn.execute(table.insert(), rows[i:i + chunk_size])
            print(f"  loaded {name}: {len(rows)} rows")