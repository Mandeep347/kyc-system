from dataclasses import dataclass
from datetime import date

from sqlalchemy import text
from sqlalchemy.engine import Connection

from validation.checks import ALL_CHECKS, Check


@dataclass(frozen=True)
class CheckResult:
    check: Check
    found: tuple[str, ...]      # sorted, de-duplicated IDs of the violating records


def run_checks(conn: Connection, as_of: date, checks: list[Check] = ALL_CHECKS) -> list[CheckResult]:
    available = {"as_of": as_of}
    results = []
    for chk in checks:
        params = {k: v for k, v in available.items() if f":{k}" in chk.sql}
        ids = conn.execute(text(chk.sql), params).scalars().all()
        results.append(CheckResult(chk, tuple(sorted(set(ids)))))
    return results