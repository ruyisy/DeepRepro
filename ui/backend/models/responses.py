"""Response models for API endpoints"""

from typing import Optional, Dict, Any, List
from datetime import datetime
from pydantic import BaseModel, Field


class TaskResponse(BaseModel):
    """Response model for task creation"""

    task_id: str
    status: str = "created"
    message: str = "Task created successfully"
    created_at: datetime = Field(default_factory=datetime.utcnow)


class BatchTaskItemResponse(BaseModel):
    """Response model for one task in a batch"""

    task_id: str
    input_source: str
    input_type: str
    label: Optional[str] = None
    order: int


class BatchTaskResponse(BaseModel):
    """Response model for batch task creation"""

    batch_id: str
    status: str = "started"
    message: str = "Batch workflow started"
    tasks: List[BatchTaskItemResponse]
    created_at: datetime = Field(default_factory=datetime.utcnow)


class WorkflowStatusResponse(BaseModel):
    """Response model for workflow status"""

    task_id: str
    status: str
    progress: int = 0
    message: str = ""
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class ConfigResponse(BaseModel):
    """Response model for configuration"""

    llm_provider: str
    available_providers: List[str]
    models: Dict[str, str]
    indexing_enabled: bool


class SettingsResponse(BaseModel):
    """Response model for settings"""

    llm_provider: str
    models: Dict[str, str]
    indexing_enabled: bool
    document_segmentation: Dict[str, Any]
    api_base_urls: Dict[str, str] = Field(default_factory=dict)
    providers: List[str] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    """Response model for errors"""

    error: str
    detail: Optional[str] = None
    code: Optional[str] = None
