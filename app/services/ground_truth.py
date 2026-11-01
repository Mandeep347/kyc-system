import csv
from pathlib import Path

from app.core.config import settings
from validation.reconcile import load_ground_truth


def _dir() -> Path:
    return Path(settings.ground_truth_dir)     # read at call time, so tests can change it


def load_truth() -> dict[str, set[str]] | None:
    """issue_type -> set of entity_ids, or None if the file is missing."""
    return load_ground_truth(_dir() / "ground_truth.csv")


def load_expected_decisions() -> dict[str, str] | None:
    """customer_id -> the kyc_status the rules say the customer should have."""
    path = _dir() / "expected_decisions.csv"
    if not path.exists():
        return None
    with path.open(newline="", encoding="utf-8") as f:
        return {row["customer_id"]: row["expected_kyc_status"] for row in csv.DictReader(f)}