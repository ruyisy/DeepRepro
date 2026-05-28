"""Request models for API endpoints"""

from typing import Dict, Any, List, Optional
from typing_extensions import Annotated
from pydantic import BaseModel, Field


class PaperToCodeRequest(BaseModel):
    """Request model for paper-to-code workflow"""

    input_source: str = Field(..., description="Path to paper file or URL")
    input_type: str = Field(..., description="Type of input: file, url")
    enable_indexing: bool = Field(default=False, description="Enable code indexing")
    workflow_mode: str = Field(
        default="raw_fast",
        description="Workflow mode: raw_fast, infer_fast, raw_deepplan, infer_deepplan",
    )
    supplementary_requirements: str = Field(
        default="",
        description="Optional user-supplied planning notes, constraints, or preferences",
    )
    planning_image_paths: List[str] = Field(
        default_factory=list,
        description="Optional user-supplied auxiliary image paths for planning only",
    )


class PaperToCodeBatchItem(BaseModel):
    """One paper input in a batch paper-to-code workflow"""

    input_source: str = Field(..., description="Path to paper file or URL")
    input_type: str = Field(..., description="Type of input: file, url")
    label: Optional[str] = Field(default=None, description="Display label for this paper")


class PaperToCodeBatchRequest(BaseModel):
    """Request model for batch paper-to-code workflows"""

    items: Annotated[List[PaperToCodeBatchItem], Field(min_length=1)] = Field(
        ..., description="Papers to process in upload order"
    )
    enable_indexing: bool = Field(default=False, description="Enable code indexing")
    workflow_mode: str = Field(
        default="raw_fast",
        description="Workflow mode: raw_fast, infer_fast, raw_deepplan, infer_deepplan",
    )
    supplementary_requirements: str = Field(
        default="",
        description="Optional user-supplied planning notes, constraints, or preferences",
    )
    planning_image_paths: List[str] = Field(
        default_factory=list,
        description="Optional user-supplied auxiliary image paths for planning only",
    )


class LLMProviderUpdateRequest(BaseModel):
    """Request model for updating LLM provider"""

    provider: str = Field(
        ..., description="LLM provider name: google, anthropic, openai"
    )


class LLMConfigUpdateRequest(BaseModel):
    """Request model for updating LLM configuration"""

    provider: str = Field(..., description="LLM provider name: google, anthropic, openai")
    default_model: str = Field(default="", description="Default model name for the selected provider")
    planning_model: str = Field(default="", description="Planning model name for the selected provider")
    subplan_model: str = Field(default="", description="Sub-plan model name for the selected provider")
    implementation_model: str = Field(default="", description="Implementation model name for the selected provider")
    base_url: str = Field(default="", description="Optional API base URL for the selected provider")
    api_key: str = Field(default="", description="Optional API key for the selected provider")


class FileUploadResponse(BaseModel):
    """Response model for file upload"""

    file_id: str
    filename: str
    path: str
    size: int


class InteractionResponseRequest(BaseModel):
    """Request model for responding to user-in-loop interactions"""

    action: str = Field(
        ..., description="User action: submit, confirm, modify, skip, cancel"
    )
    data: Dict[str, Any] = Field(
        default_factory=dict,
        description="Response data (e.g., answers to questions, modification feedback)",
    )
    skipped: bool = Field(default=False, description="Whether user chose to skip")
