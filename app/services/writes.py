from datetime import date, datetime, timedelta

from sqlalchemy import Integer, cast, func, select, text
from sqlalchemy.orm import Session

from app.core.enums import AuditAction
from app.models import AuditEvent, DocumentRequirementLink, Verification
from generator import ids      # the ONE place ID formats are defined

WRITE_LOCK_KEY = 5_150_001      # any constant: "only one writer at a time"

# kind -> (primary key column, function that formats a number into an ID)
_ID_SOURCES = {
    "audit": (AuditEvent.event_id, ids.audit_id),
    "verification": (Verification.verification_id, ids.verification_id),
    "link": (DocumentRequirementLink.link_id, ids.link_id),
}


class Ledger:
    """One per write request. Takes the write lock, hands out IDs and timestamps,
    and appends audit events. Nothing is saved until the caller commits."""

    def __init__(self, db: Session, as_of: date):
        self.db, self.as_of = db, as_of
        # released automatically when the transaction ends (commit or rollback)
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": WRITE_LOCK_KEY})
        self._last: datetime | None = db.scalar(select(func.max(AuditEvent.occurred_at)))
        self._counters: dict[str, int] = {}

    def next_id(self, kind: str) -> str:
        col, fmt = _ID_SOURCES[kind]
        if kind not in self._counters:
            # 'AUD-000123' -> characters from position 5 on -> 123
            self._counters[kind] = db_max = self.db.scalar(
                select(func.coalesce(func.max(cast(func.substr(col, 5), Integer)), 0)))
            assert db_max is not None
        self._counters[kind] += 1
        return fmt(self._counters[kind])

    def stamp(self) -> datetime:
        """As-of date + current time of day, but never earlier than the newest audit event.
        That keeps 'event IDs follow time order' true."""
        candidate = datetime.combine(self.as_of, datetime.now().time()).replace(microsecond=0)
        if self._last is not None and candidate <= self._last:
            candidate = self._last + timedelta(seconds=1)
        self._last = candidate
        return candidate

    def audit(self, customer_id: str, entity_type: str, entity_id: str, action: AuditAction,
              old: dict | None, new: dict | None, actor: str,
              when: datetime | None = None) -> datetime:
        when = when or self.stamp()
        self.db.add(AuditEvent(
            event_id=self.next_id("audit"), customer_id=customer_id, entity_type=entity_type,
            entity_id=entity_id, action=action.value, old_value=old, new_value=new,
            actor=actor, occurred_at=when))
        return when