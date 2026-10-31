from datetime import date

from app.core import rules
from app.core.enums import CustomerTypeCode as T
from app.core.enums import DocumentCategory as C
from app.core.enums import RiskLevel as R
from generator import ids

REQUIREMENT_YEAR = 2026
REQUIREMENT_EFFECTIVE_FROM = date(2026, 1, 1)

TYPE_ID = {
    T.INDIVIDUAL: ids.customer_type_id(1),
    T.SOLE_PROPRIETOR: ids.customer_type_id(2),
    T.COMPANY: ids.customer_type_id(3),
}


def customer_type_rows() -> list[dict]:
    return [
        {"customer_type_id": TYPE_ID[T.INDIVIDUAL], "type_code": T.INDIVIDUAL.value,
         "display_name": "Individual", "is_business": False,
         "requires_beneficial_owner": False},
        {"customer_type_id": TYPE_ID[T.SOLE_PROPRIETOR], "type_code": T.SOLE_PROPRIETOR.value,
         "display_name": "Sole Proprietor", "is_business": True,
         "requires_beneficial_owner": True},
        {"customer_type_id": TYPE_ID[T.COMPANY], "type_code": T.COMPANY.value,
         "display_name": "Company", "is_business": True,
         "requires_beneficial_owner": True},
    ]


# Documents every customer of this type needs, at ANY risk level: (category, mandatory)
_BASE = {
    T.INDIVIDUAL: [(C.ID_PROOF, True), (C.ADDRESS_PROOF, True)],
    T.SOLE_PROPRIETOR: [(C.ID_PROOF, True), (C.TAX_ID, True),
                        (C.BUSINESS_ADDRESS_PROOF, True)],
    T.COMPANY: [(C.INCORPORATION_CERT, True), (C.TAX_ID, True),
                (C.BUSINESS_ADDRESS_PROOF, True), (C.OWNERSHIP_PROOF, True)],
}


def _categories_for(type_code: T, risk: R) -> list[tuple[C, bool]]:
    cats = list(_BASE[type_code])
    if risk == R.MEDIUM and type_code == T.INDIVIDUAL:
        cats.append((C.SOURCE_OF_FUNDS, False))   # optional: tests "optional != missing"
    if risk == R.HIGH:
        cats.append((C.SOURCE_OF_FUNDS, True))    # enhanced due diligence
    return cats


def requirement_rows() -> list[dict]:
    rows, n = [], 0
    for type_code in T:
        for risk in R:
            for category, mandatory in _categories_for(type_code, risk):
                n += 1
                is_address = category in (C.ADDRESS_PROOF, C.BUSINESS_ADDRESS_PROOF)
                lead_days = rules.HIGH_RISK_LEAD_DAYS if risk == R.HIGH else rules.DEFAULT_LEAD_DAYS
                rows.append({
                    "requirement_id": ids.requirement_id(n, REQUIREMENT_YEAR),
                    "customer_type_id": TYPE_ID[type_code],
                    "risk_level": risk.value,
                    "document_category": category.value,
                    "is_mandatory": mandatory,
                    "max_age_days": rules.ADDRESS_PROOF_MAX_AGE_DAYS if is_address else None,
                    "expiry_lead_days": list(lead_days),
                    "effective_from": REQUIREMENT_EFFECTIVE_FROM,
                    "effective_to": None,
                })
    return rows