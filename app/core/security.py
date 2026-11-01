from dataclasses import dataclass
from enum import StrEnum

from fastapi import Depends, Header, HTTPException


class Role(StrEnum):
    CUSTOMER = "CUSTOMER"    # the account holder
    ANALYST = "ANALYST"      # operations analyst
    REVIEWER = "REVIEWER"    # risk / compliance reviewer
    ADMIN = "ADMIN"          # system administrator


@dataclass(frozen=True)
class Principal:
    role: Role
    user: str     # becomes the `actor` of every audit event


def get_principal(
    x_role: str | None = Header(None, description="CUSTOMER, ANALYST, REVIEWER or ADMIN"),
    x_user: str | None = Header(None, description="Your user name, e.g. analyst_01 "
                                                 "(for CUSTOMER: your customer ID)"),
) -> Principal:
    """DEMO identity taken from headers. Replace with real authentication (JWT/OAuth)
    in production; everything else in the API stays the same."""
    if not x_role or not x_user:
        raise HTTPException(status_code=401, detail="Send the X-Role and X-User headers")
    try:
        role = Role(x_role.strip().upper())
    except ValueError:
        raise HTTPException(status_code=401, detail=f"Unknown role {x_role!r}")
    if len(x_user) > 40:     # audit_events.actor is varchar(40)
        raise HTTPException(status_code=400, detail="X-User must be at most 40 characters")
    return Principal(role=role, user=x_user.strip())


def require_roles(*allowed: Role):
    """Dependency factory: `Depends(require_roles(Role.ANALYST, Role.ADMIN))`."""
    def dependency(principal: Principal = Depends(get_principal)) -> Principal:
        if principal.role not in allowed:
            names = ", ".join(r.value for r in allowed)
            raise HTTPException(status_code=403,
                                detail=f"Role {principal.role.value} may not do this. Allowed: {names}")
        return principal
    return dependency