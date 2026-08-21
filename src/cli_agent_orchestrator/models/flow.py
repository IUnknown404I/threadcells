"""Flow model."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from cli_agent_orchestrator.constants import DEFAULT_PROVIDER


class Flow(BaseModel):
    """Flow model - represents a scheduled agent session."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., description="Unique flow identifier")
    file_path: str = Field(..., description="Path to flow definition file")
    schedule: str = Field(..., description="Cron expression")
    agent_profile: str = Field(..., description="Agent profile to use")
    provider: str = Field(default=DEFAULT_PROVIDER, description="Provider to use")
    script: str = Field("", description="Path to poll script (optional)")
    last_run: Optional[datetime] = Field(None, description="Last execution time")
    next_run: Optional[datetime] = Field(None, description="Next scheduled execution time")
    enabled: bool = Field(True, description="Whether flow is enabled")
    prompt_template: Optional[str] = Field(None, description="Prompt template text")
    project_id: Optional[str] = Field(None, alias="projectId", description="Launch project ID")
    project_name: Optional[str] = Field(None, description="Historical launch project name")
    project_path: Optional[str] = Field(None, description="Historical launch project path")
    project_description: Optional[str] = Field(
        None, description="Historical launch project description"
    )
