"""Durable project registry models."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class Project(BaseModel):
    """One server-authoritative launch project."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(..., alias="projectId", description="Stable project identifier")
    name: str
    path: str
    description: Optional[str] = None
    is_default: bool = Field(False, alias="isDefault")
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class UpdateProject(BaseModel):
    """Editable project registry fields; launch snapshots are never edited."""

    model_config = ConfigDict(populate_by_name=True)

    name: Optional[str] = None
    path: Optional[str] = None
    description: Optional[str] = None
    is_default: Optional[bool] = Field(None, alias="isDefault")
