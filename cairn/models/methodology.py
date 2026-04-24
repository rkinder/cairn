from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class MethodologyKind(str, Enum):
    sigma = "sigma"
    procedure = "procedure"


class ProcedureMethodology(BaseModel):
    title: str = Field(...)
    tags: list[str] = Field(...)
    steps: list[str] = Field(..., min_length=2)
    description: str | None = Field(default=None)
    references: list[str] = Field(default_factory=list)
    author: str | None = Field(default=None)
    severity: Literal["low", "medium", "high", "critical"] | None = Field(default=None)


class SigmaMethodology(BaseModel):
    title: str = Field(...)
    tags: list[str] = Field(default_factory=list)
    description: str | None = Field(default=None)
    status: str | None = Field(default=None)
