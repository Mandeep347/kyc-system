"""Every validation check. A check is ONE SQL query that returns the IDs of the
violating records in a column called entity_id."""
from dataclasses import dataclass

import app.models  # noqa: F401  (registers the tables on Base.metadata)
from app.core import rules
from app.core.database import Base
from app.core.enums import (AlertStatus, AlertType, AuditAction, CustomerTypeCode,
                            DocumentCategory, DocumentStatus, DocumentType, IssueType,
                            KycStatus, ReviewResolution, ReviewStatus, ReviewTrigger,
                            RiskLevel, Severity, VerificationResult)

AS_OF = "CAST(:as_of AS date)"    # the only bind parameter; supplied by the runner


@dataclass(frozen=True)
class Check:
    check_id: str
    category: str
    description: str
    sql: str
    # None  -> the check must find NOTHING (any finding is a failure)
    # set   -> findings are expected and must equal the ground truth for that issue
    issue_type: IssueType | None = None


def _pk(table) -> str:
    return next(iter(table.primary_key.columns)).name


# ============================================================ 1. primary keys

def _primary_key_checks() -> list[Check]:
    return [
        Check(f"PK.{t.name}", "Primary key", f"{t.name}.{_pk(t)} has no duplicates",
              f'SELECT "{_pk(t)}" AS entity_id FROM "{t.name}" '
              f'GROUP BY "{_pk(t)}" HAVING count(*) > 1')
        for t in Base.metadata.sorted_tables
    ]


# ============================================================ 2. foreign keys

def _foreign_key_checks() -> list[Check]:
    out = []
    for t in Base.metadata.sorted_tables:
        for fk in sorted(t.foreign_keys, key=lambda f: f.parent.name):
            child, parent = fk.parent.name, fk.column
            out.append(Check(
                f"FK.{t.name}.{child}", "Foreign key",
                f"{t.name}.{child} points to an existing {parent.table.name}.{parent.name}",
                f'SELECT c."{_pk(t)}" AS entity_id FROM "{t.name}" c '
                f'LEFT JOIN "{parent.table.name}" p ON p."{parent.name}" = c."{child}" '
                f'WHERE c."{child}" IS NOT NULL AND p."{parent.name}" IS NULL'))
    return out


# ============================================================ 3. ID formats

ID_PATTERNS = {
    "customer_types": "^CT-[0-9]{2}$",
    "kyc_requirements": "^KYC-[0-9]{4}-[0-9]{6}$",
    "customers": "^CUS-[0-9]{6}$",
    "documents": "^DOC-CUS-[0-9]{6}-[0-9]{3}$",
    "verifications": "^VER-[0-9]{6}$",
    "risk_ratings": "^RSK-[0-9]{6}$",
    "review_cases": "^REV-CUS-[0-9]{6}-[0-9]{4}$",
    "beneficial_owners": "^BO-[0-9]{6}$",
    "alerts": "^ALT-[0-9]{4}-[0-9]{6}$",
    "audit_events": "^AUD-[0-9]{6}$",
    "document_requirement_links": "^LNK-[0-9]{6}$",
}


def _id_format_checks() -> list[Check]:
    out = []
    for name, pattern in ID_PATTERNS.items():
        pk = _pk(Base.metadata.tables[name])
        out.append(Check(
            f"ID.{name}", "ID format", f"{name}.{pk} matches {pattern}",
            f"SELECT \"{pk}\" AS entity_id FROM \"{name}\" WHERE NOT (\"{pk}\" ~ '{pattern}')"))
    return out


# ============================================================ 4. allowed enum values

ENUM_COLUMNS = [
    ("customer_types", "type_code", CustomerTypeCode),
    ("kyc_requirements", "risk_level", RiskLevel),
    ("kyc_requirements", "document_category", DocumentCategory),
    ("customers", "kyc_status", KycStatus),
    ("documents", "document_category", DocumentCategory),
    ("documents", "document_type", DocumentType),
    ("documents", "status", DocumentStatus),
    ("verifications", "result", VerificationResult),
    ("risk_ratings", "risk_level", RiskLevel),
    ("review_cases", "trigger", ReviewTrigger),
    ("review_cases", "status", ReviewStatus),
    ("review_cases", "resolution", ReviewResolution),
    ("alerts", "alert_type", AlertType),
    ("alerts", "severity", Severity),
    ("alerts", "status", AlertStatus),
    ("audit_events", "action", AuditAction),
]


def _enum_checks() -> list[Check]:
    out = []
    for table, col, enum_cls in ENUM_COLUMNS:
        pk = _pk(Base.metadata.tables[table])
        allowed = ", ".join(f"'{m.value}'" for m in enum_cls)
        out.append(Check(
            f"ENUM.{table}.{col}", "Allowed values", f"{table}.{col} is one of {enum_cls.__name__}",
            f'SELECT "{pk}" AS entity_id FROM "{table}" '
            f'WHERE "{col}" IS NOT NULL AND "{col}" NOT IN ({allowed})'))
    return out


# ============================================================ 5. date ordering

_DATE = "Date ordering"
_DATE_CHECKS = [
    Check("DATE.document_expiry_after_issue", _DATE, "document expiry_date is after issue_date",
          "SELECT document_id AS entity_id FROM documents "
          "WHERE expiry_date IS NOT NULL AND expiry_date <= issue_date"),
    Check("DATE.document_submitted_in_range", _DATE,
          "document issue_date <= submitted_on <= as_of",
          f"SELECT document_id AS entity_id FROM documents "
          f"WHERE submitted_on < issue_date OR submitted_on > {AS_OF}"),
    Check("DATE.verification_after_submission", _DATE,
          "a verification is never earlier than the document's submission",
          "SELECT v.verification_id AS entity_id FROM verifications v "
          "JOIN documents d ON d.document_id = v.document_id "
          "WHERE v.verified_at < d.submitted_on"),
    Check("DATE.verification_not_in_future", _DATE, "no verification after the as-of date",
          f"SELECT verification_id AS entity_id FROM verifications "
          f"WHERE verified_at >= {AS_OF} + 1"),
    Check("DATE.review_due_after_scheduled", _DATE, "review due_date >= scheduled_date",
          "SELECT review_id AS entity_id FROM review_cases WHERE due_date < scheduled_date"),
    Check("DATE.review_closed_in_range", _DATE,
          "review closed_at is between scheduled_date and the as-of date",
          f"SELECT review_id AS entity_id FROM review_cases WHERE closed_at IS NOT NULL "
          f"AND (closed_at < scheduled_date OR closed_at >= {AS_OF} + 1)"),
    Check("DATE.rating_period_ordered", _DATE, "risk rating effective_to >= effective_from",
          "SELECT rating_id AS entity_id FROM risk_ratings "
          "WHERE effective_to IS NOT NULL AND effective_to < effective_from"),
    Check("DATE.rating_periods_overlap", _DATE, "a customer's rating periods never overlap",
          "SELECT DISTINCT a.rating_id AS entity_id FROM risk_ratings a "
          "JOIN risk_ratings b ON a.customer_id = b.customer_id AND a.rating_id < b.rating_id "
          "AND a.effective_from <= COALESCE(b.effective_to, DATE '9999-12-31') "
          "AND b.effective_from <= COALESCE(a.effective_to, DATE '9999-12-31')"),
    Check("DATE.rating_starts_at_onboarding", _DATE, "no rating starts before the customer onboarded",
          "SELECT r.rating_id AS entity_id FROM risk_ratings r "
          "JOIN customers c ON c.customer_id = r.customer_id WHERE r.effective_from < c.onboarded_on"),
    Check("DATE.owner_period_ordered", _DATE, "owner effective_to >= effective_from",
          "SELECT owner_id AS entity_id FROM beneficial_owners "
          "WHERE effective_to IS NOT NULL AND effective_to < effective_from"),
    Check("DATE.owner_starts_after_onboarding", _DATE, "no owner starts before the customer onboarded",
          "SELECT o.owner_id AS entity_id FROM beneficial_owners o "
          "JOIN customers c ON c.customer_id = o.customer_id WHERE o.effective_from < c.onboarded_on"),
    Check("DATE.customer_onboarded_not_future", _DATE, "onboarded_on <= as_of",
          f"SELECT customer_id AS entity_id FROM customers WHERE onboarded_on > {AS_OF}"),
    Check("DATE.customer_adult_at_onboarding", _DATE, "individuals were at least 18 when onboarded",
          "SELECT customer_id AS entity_id FROM customers WHERE date_of_birth IS NOT NULL "
          "AND date_of_birth > CAST(onboarded_on - INTERVAL '18 years' AS date)"),
    Check("DATE.business_incorporated_before_onboarding", _DATE,
          "businesses were incorporated on or before onboarding",
          "SELECT customer_id AS entity_id FROM customers "
          "WHERE incorporation_date IS NOT NULL AND incorporation_date > onboarded_on"),
    Check("DATE.alert_in_window", _DATE, "alert trigger_date is in the as-of year, up to as_of",
          f"SELECT alert_id AS entity_id FROM alerts WHERE trigger_date > {AS_OF} "
          f"OR trigger_date < make_date(CAST(EXTRACT(year FROM {AS_OF}) AS integer), 1, 1)"),
    Check("DATE.alert_created_on_trigger_date", _DATE, "alert created_at falls on its trigger_date",
          "SELECT alert_id AS entity_id FROM alerts WHERE CAST(created_at AS date) <> trigger_date"),
    Check("DATE.audit_not_in_future", _DATE, "no audit event after the as-of date",
          f"SELECT event_id AS entity_id FROM audit_events WHERE occurred_at >= {AS_OF} + 1"),
]


# ============================================================ 6. reconciliation / derived values

_REC = "Reconciliation"
_STATUS_CASE = "".join(f"WHEN '{r.value}' THEN '{s.value}' "
                       for r, s in rules.DOC_STATUS_FROM_RESULT.items())
_RISK_CASE = "".join(f"WHEN risk_score BETWEEN {lo} AND {hi} THEN '{lvl.value}' "
                     for lvl, (lo, hi) in rules.RISK_BANDS.items())
_CADENCE_CASE = "".join(f"WHEN '{lvl.value}' THEN {m} "
                        for lvl, m in rules.REVIEW_CADENCE_MONTHS.items())
_DEFAULT_LEADS = "ARRAY[" + ",".join(map(str, rules.DEFAULT_LEAD_DAYS)) + "]"

_REC_CHECKS = [
    Check("REC.document_status_matches_latest_verification", _REC,
          "document status equals the result of its latest verification",
          "WITH last AS (SELECT DISTINCT ON (document_id) document_id, result FROM verifications "
          "ORDER BY document_id, verified_at DESC, verification_id DESC) "
          "SELECT d.document_id AS entity_id FROM documents d "
          "LEFT JOIN last l ON l.document_id = d.document_id "
          f"WHERE d.status <> CASE l.result {_STATUS_CASE} ELSE 'PENDING' END"),
    Check("REC.name_score_matches_result", _REC,
          f"NAME_MISMATCH exactly when name_match_score < {rules.NAME_MATCH_THRESHOLD}",
          f"SELECT verification_id AS entity_id FROM verifications "
          f"WHERE (result = 'NAME_MISMATCH') <> (name_match_score < {rules.NAME_MATCH_THRESHOLD})"),
    Check("REC.expiring_alert_trigger_date", _REC, "DOC_EXPIRING trigger_date = expiry_date - lead_days",
          "SELECT a.alert_id AS entity_id FROM alerts a JOIN documents d ON d.document_id = a.document_id "
          "WHERE a.alert_type = 'DOC_EXPIRING' "
          "AND (d.expiry_date IS NULL OR a.trigger_date <> d.expiry_date - a.lead_days)"),
    Check("REC.expired_alert_trigger_date", _REC, "DOC_EXPIRED trigger_date = expiry_date + 1 day",
          "SELECT a.alert_id AS entity_id FROM alerts a JOIN documents d ON d.document_id = a.document_id "
          "WHERE a.alert_type = 'DOC_EXPIRED' "
          "AND (d.expiry_date IS NULL OR a.trigger_date <> d.expiry_date + 1)"),
    Check("REC.alert_lead_time_configured", _REC,
          "DOC_EXPIRING lead_days is one of the requirement's configured lead times",
          "SELECT a.alert_id AS entity_id FROM alerts a "
          "LEFT JOIN document_requirement_links l ON l.document_id = a.document_id "
          "LEFT JOIN kyc_requirements q ON q.requirement_id = l.requirement_id "
          "WHERE a.alert_type = 'DOC_EXPIRING' "
          f"AND NOT (a.lead_days = ANY (COALESCE(q.expiry_lead_days, {_DEFAULT_LEADS})))"),
    Check("REC.review_due_follows_risk_cadence", _REC,
          "review due_date = onboarded_on + the cadence of the current risk level",
          "SELECT r.review_id AS entity_id FROM review_cases r "
          "JOIN customers c ON c.customer_id = r.customer_id "
          "JOIN risk_ratings k ON k.customer_id = c.customer_id AND k.effective_to IS NULL "
          "WHERE r.\"trigger\" = 'PERIODIC' AND r.due_date <> CAST(c.onboarded_on + "
          f"(CASE k.risk_level {_CADENCE_CASE} END) * INTERVAL '1 month' AS date)"),
    Check("REC.risk_level_matches_score", _REC, "risk_level is derived from risk_score",
          f"SELECT rating_id AS entity_id FROM risk_ratings "
          f"WHERE risk_level <> CASE {_RISK_CASE} END"),
    Check("REC.one_current_rating_per_customer", _REC, "every customer has exactly one current rating",
          "SELECT c.customer_id AS entity_id FROM customers c WHERE "
          "(SELECT count(*) FROM risk_ratings r WHERE r.customer_id = c.customer_id "
          "AND r.effective_to IS NULL) <> 1"),
    Check("REC.document_id_embeds_customer", _REC, "DOC-<customer_id>-NNN embeds its own customer",
          "SELECT document_id AS entity_id FROM documents "
          "WHERE NOT starts_with(document_id, 'DOC-' || customer_id || '-')"),
    Check("REC.document_id_sequence", _REC, "a customer's document numbers run 001..N without gaps",
          "SELECT customer_id AS entity_id FROM documents GROUP BY customer_id "
          "HAVING count(*) <> max(CAST(right(document_id, 3) AS integer))"),
    Check("REC.review_id_embeds_customer_year", _REC, "REV-<customer_id>-<year> matches its columns",
          "SELECT review_id AS entity_id FROM review_cases "
          "WHERE review_id <> 'REV-' || customer_id || '-' || CAST(review_year AS text)"),
    Check("REC.audit_chronological_ids", _REC, "audit event IDs follow time order",
          "SELECT event_id AS entity_id FROM (SELECT event_id, occurred_at, "
          "LAG(occurred_at) OVER (ORDER BY event_id) AS prev FROM audit_events) t "
          "WHERE prev > occurred_at"),
    Check("REC.audit_entity_exists", _REC, "audit_events.entity_id points to a real record",
          "SELECT e.event_id AS entity_id FROM audit_events e WHERE NOT ("
          "(e.entity_type = 'CUSTOMER' AND EXISTS (SELECT 1 FROM customers x WHERE x.customer_id = e.entity_id)) OR "
          "(e.entity_type = 'DOCUMENT' AND EXISTS (SELECT 1 FROM documents x WHERE x.document_id = e.entity_id)) OR "
          "(e.entity_type = 'REVIEW_CASE' AND EXISTS (SELECT 1 FROM review_cases x WHERE x.review_id = e.entity_id)) OR "
          "(e.entity_type = 'ALERT' AND EXISTS (SELECT 1 FROM alerts x WHERE x.alert_id = e.entity_id)) OR "
          "(e.entity_type = 'RISK_RATING' AND EXISTS (SELECT 1 FROM risk_ratings x WHERE x.rating_id = e.entity_id)))"),
]

# "the last audit state must equal the stored state" for the 4 stateful entities
_LAST_STATE = [("CUSTOMER", "customers", "customer_id", "kyc_status"),
               ("DOCUMENT", "documents", "document_id", "status"),
               ("REVIEW_CASE", "review_cases", "review_id", "status"),
               ("ALERT", "alerts", "alert_id", "status")]
_REC_CHECKS += [
    Check(f"REC.audit_state_{table}", _REC,
          f"{table}.{col} equals the last state in the audit trail (and a trail exists)",
          "WITH last AS (SELECT DISTINCT ON (entity_id) entity_id, "
          "COALESCE(new_value->>'kyc_status', new_value->>'status') AS s FROM audit_events "
          f"WHERE entity_type = '{etype}' ORDER BY entity_id, occurred_at DESC, event_id DESC) "
          f"SELECT t.\"{pk}\" AS entity_id FROM \"{table}\" t "
          f"LEFT JOIN last l ON l.entity_id = t.\"{pk}\" WHERE l.s IS DISTINCT FROM t.\"{col}\"")
    for etype, table, pk, col in _LAST_STATE
]


# ============================================================ 7. lifecycle (audit replay)

_STATE_EVENTS = (
    "WITH e AS (SELECT entity_type, entity_id, "
    "COALESCE(old_value->>'kyc_status', old_value->>'status') AS old_s, "
    "COALESCE(new_value->>'kyc_status', new_value->>'status') AS new_s, "
    "LAG(COALESCE(new_value->>'kyc_status', new_value->>'status')) "
    "OVER (PARTITION BY entity_type, entity_id ORDER BY occurred_at, event_id) AS prev_s "
    "FROM audit_events WHERE entity_type IN ('CUSTOMER', 'DOCUMENT', 'REVIEW_CASE', 'ALERT')) ")
_ALLOWED_VALUES = ", ".join(f"('{t}', '{o}', '{n}')"
                            for t, pairs in sorted(rules.ALLOWED_TRANSITIONS.items())
                            for o, n in sorted(pairs))

_LIFE_CHECKS = [
    Check("LIFE.audit_chain_continuous", "Lifecycle",
          "each audit event starts from the state the previous event ended in",
          _STATE_EVENTS + "SELECT DISTINCT entity_id FROM e WHERE old_s IS DISTINCT FROM prev_s"),
    Check("LIFE.invalid_transition", "Lifecycle",
          "every status change in the audit trail is an allowed transition",
          _STATE_EVENTS + "SELECT DISTINCT e.entity_id FROM e WHERE e.old_s IS NOT NULL AND NOT EXISTS "
          f"(SELECT 1 FROM (VALUES {_ALLOWED_VALUES}) AS a(t, o, n) "
          "WHERE a.t = e.entity_type AND a.o = e.old_s AND a.n = e.new_s)",
          IssueType.EXPIRED_BUT_VALID),
]


# ============================================================ 8. duplicates

_DUP_CHECKS = [
    Check("DUP.document_number_across_customers", "Duplicates",
          "a document number is not shared by different customers",
          "SELECT document_id AS entity_id FROM documents WHERE document_number IN "
          "(SELECT document_number FROM documents GROUP BY document_number "
          "HAVING count(DISTINCT customer_id) > 1)",
          IssueType.DUPLICATE_DOCUMENT),
    Check("DUP.document_number_within_customer", "Duplicates",
          "a customer does not hold the same document number twice",
          "SELECT document_id AS entity_id FROM (SELECT document_id, row_number() OVER "
          "(PARTITION BY customer_id, document_number ORDER BY document_id) AS rn FROM documents) t "
          "WHERE rn > 1"),
    Check("DUP.alert_raised_twice", "Duplicates", "the same alert is never raised twice",
          "SELECT alert_id AS entity_id FROM (SELECT alert_id, row_number() OVER (PARTITION BY "
          "alert_type, COALESCE(document_id, ''), COALESCE(review_id, ''), COALESCE(lead_days, 0) "
          "ORDER BY alert_id) AS rn FROM alerts) t WHERE rn > 1"),
]


# ============================================================ 9. business rules (expected zero)

_RULE = "Business rule"
_ADDRESS = "('" + "', '".join((DocumentCategory.ADDRESS_PROOF.value,
                               DocumentCategory.BUSINESS_ADDRESS_PROOF.value)) + "')"
_SPEC_VALUES = ", ".join(f"('{cat.value}', '{dt.value}')"
                         for cat, specs in rules.DOCUMENT_SPECS.items() for dt, _ in specs)

_RULE_CHECKS = [
    Check("RULE.ownership_under_100", _RULE, "active ownership never totals less than 100%",
          f"SELECT customer_id AS entity_id FROM beneficial_owners WHERE effective_to IS NULL "
          f"GROUP BY customer_id HAVING sum(ownership_pct) < {rules.OWNERSHIP_TOTAL}"),
    Check("RULE.resolution_only_when_closed", _RULE, "only CLOSED reviews carry a resolution",
          "SELECT review_id AS entity_id FROM review_cases "
          "WHERE resolution IS NOT NULL AND status <> 'CLOSED'"),
    Check("RULE.owner_on_non_business", _RULE, "beneficial owners belong to business customers only",
          "SELECT o.owner_id AS entity_id FROM beneficial_owners o "
          "JOIN customers c ON c.customer_id = o.customer_id "
          "JOIN customer_types t ON t.customer_type_id = c.customer_type_id WHERE NOT t.is_business"),
    Check("RULE.customer_type_matches_dates", _RULE,
          "businesses have an incorporation date, individuals do not",
          "SELECT c.customer_id AS entity_id FROM customers c "
          "JOIN customer_types t ON t.customer_type_id = c.customer_type_id "
          "WHERE t.is_business <> (c.incorporation_date IS NOT NULL)"),
    Check("RULE.document_type_allowed_for_category", _RULE, "document_type is valid for its category",
          "SELECT d.document_id AS entity_id FROM documents d WHERE NOT EXISTS "
          f"(SELECT 1 FROM (VALUES {_SPEC_VALUES}) AS s(cat, typ) "
          "WHERE s.cat = d.document_category AND s.typ = d.document_type)"),
    Check("RULE.address_proof_age", _RULE,
          f"address proof is at most {rules.ADDRESS_PROOF_MAX_AGE_DAYS} days old when submitted",
          f"SELECT document_id AS entity_id FROM documents WHERE document_category IN {_ADDRESS} "
          f"AND submitted_on - issue_date > {rules.ADDRESS_PROOF_MAX_AGE_DAYS}"),
]


# ============================================================ 10. planted-anomaly detectors

_ANOM = "Planted-anomaly detection"
_ANOMALY_CHECKS = [
    Check("ANOM.expired_but_valid", _ANOM, "documents marked VALID whose expiry_date has passed",
          f"SELECT document_id AS entity_id FROM documents "
          f"WHERE status = 'VALID' AND expiry_date < {AS_OF}",
          IssueType.EXPIRED_BUT_VALID),
    Check("ANOM.approved_missing_evidence", _ANOM,
          "APPROVED customers missing a valid, unexpired mandatory document",
          "SELECT DISTINCT c.customer_id AS entity_id FROM customers c "
          "JOIN risk_ratings r ON r.customer_id = c.customer_id AND r.effective_to IS NULL "
          "JOIN kyc_requirements q ON q.customer_type_id = c.customer_type_id "
          "AND q.risk_level = r.risk_level AND q.is_mandatory "
          f"AND q.effective_from <= {AS_OF} AND (q.effective_to IS NULL OR q.effective_to >= {AS_OF}) "
          "WHERE c.kyc_status = 'APPROVED' AND NOT EXISTS (SELECT 1 FROM documents d "
          "WHERE d.customer_id = c.customer_id AND d.document_category = q.document_category "
          f"AND d.status = 'VALID' AND (d.expiry_date IS NULL OR d.expiry_date >= {AS_OF}))",
          IssueType.APPROVED_MISSING_EVIDENCE),
    Check("ANOM.ownership_over_100", _ANOM, "active ownership totals more than 100%",
          f"SELECT customer_id AS entity_id FROM beneficial_owners WHERE effective_to IS NULL "
          f"GROUP BY customer_id HAVING sum(ownership_pct) > {rules.OWNERSHIP_TOTAL}",
          IssueType.OWNERSHIP_OVER_100),
    Check("ANOM.review_closed_no_resolution", _ANOM, "CLOSED reviews with no resolution",
          "SELECT review_id AS entity_id FROM review_cases WHERE status = 'CLOSED' AND resolution IS NULL",
          IssueType.REVIEW_CLOSED_NO_RESOLUTION),
    Check("ANOM.missing_ownership", _ANOM, "business customers with no active beneficial owner",
          "SELECT c.customer_id AS entity_id FROM customers c "
          "JOIN customer_types t ON t.customer_type_id = c.customer_type_id "
          "WHERE t.requires_beneficial_owner AND NOT EXISTS (SELECT 1 FROM beneficial_owners b "
          "WHERE b.customer_id = c.customer_id AND b.effective_to IS NULL)",
          IssueType.MISSING_OWNERSHIP),
]


ALL_CHECKS: list[Check] = (
    _primary_key_checks() + _foreign_key_checks() + _id_format_checks() + _enum_checks()
    + _DATE_CHECKS + _REC_CHECKS + _LIFE_CHECKS + _DUP_CHECKS + _RULE_CHECKS + _ANOMALY_CHECKS
)