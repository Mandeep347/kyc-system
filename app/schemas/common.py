from decimal import Decimal
from typing import Annotated, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, PlainSerializer

T = TypeVar("T")

# Decimal inside Python (exact maths), a plain JSON number on the wire
Number = Annotated[Decimal, PlainSerializer(float, return_type=float, when_used="always")]


class ORMModel(BaseModel):
    """Base for schemas that can be built straight from SQLAlchemy objects."""
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    total: int      # rows matching the filters, ignoring limit/offset
    limit: int
    offset: int
    items: list[T]