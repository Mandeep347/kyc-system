from enum import Enum

def in_list(column: str, enum_cls: type[Enum]) -> str:
    """in_list('status', KycStatus) -> status IN ('PENDING', 'APPROVED', ...)"""
    values = ", ".join(f"'{member.value}'" for member in enum_cls)
    return f"{column} IN ({values})"