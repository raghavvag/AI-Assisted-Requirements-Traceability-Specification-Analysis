"""
backend/app/schemas.py
=======================
Pydantic v2 request/response schemas for all API endpoints.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class GoogleAuthRequest(BaseModel):
    id_token: str = Field(..., description="Google ID token from the frontend OAuth flow")


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: "UserOut"


class UserOut(BaseModel):
    id: int
    email: str
    name: str
    avatar_url: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=1000)


class ProjectOut(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    created_at: datetime
    has_analysis: bool = False
    doc_count: int = 0

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

class DocTypeEnum(str, Enum):
    SRS    = "SRS"
    API_V1 = "API_V1"
    API_V2 = "API_V2"


class DocumentOut(BaseModel):
    id: int
    doc_type: DocTypeEnum
    original_filename: Optional[str] = None
    uploaded_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# SRS extraction (industrial .docx / .pdf / .md ingestion)
# ---------------------------------------------------------------------------

class RequirementSource(BaseModel):
    file: str = ""
    format: str = ""
    locator: str = ""


class ExtractedRequirement(BaseModel):
    id: str
    title: str
    text: str
    type: Optional[str] = None
    priority: Optional[str] = None
    section: Optional[str] = None
    raw_id: Optional[str] = None
    id_source: Optional[str] = None
    acceptance_criteria: Optional[str] = None
    rationale: Optional[str] = None
    origin: Optional[str] = None
    strategy: Optional[str] = None
    source: Optional[RequirementSource] = None


class ExtractionReportOut(BaseModel):
    source_file: str = ""
    format: str = ""
    n_requirements: int = 0
    n_blocks: int = 0
    by_strategy: Dict[str, int] = {}
    skipped: Dict[str, int] = {}
    duplicates: List[str] = []
    warnings: List[str] = []


class ExtractionSummary(BaseModel):
    """Returned with an SRS upload so the UI can confirm what was found."""
    n_requirements: int
    format: str
    by_strategy: Dict[str, int] = {}
    n_warnings: int = 0
    warnings: List[str] = []
    requirement_ids: List[str] = []


class DocumentUploadOut(DocumentOut):
    extraction: Optional[ExtractionSummary] = None


class SrsRequirementsOut(BaseModel):
    filename: str
    is_default: bool = False
    requirements: List[ExtractedRequirement]
    report: ExtractionReportOut


# ---------------------------------------------------------------------------
# Analysis Pipeline
# ---------------------------------------------------------------------------

class AnalyzeRequest(BaseModel):
    threshold: float = Field(default=0.55, ge=0.0, le=1.0)
    drift_threshold: float = Field(default=-0.10, le=0.0, ge=-1.0)
    enable_drift: bool = True
    enable_llm_explain: bool = False   # expensive, off by default


# --- Matrix Response ---

class RequirementNode(BaseModel):
    id: str
    title: str
    text: str
    # present when the SRS was a formal document (.docx / .pdf)
    type: Optional[str] = None
    priority: Optional[str] = None
    section: Optional[str] = None


class EndpointNode(BaseModel):
    id: str
    method: str
    path: str
    summary: str


class MatrixCell(BaseModel):
    req_id: str
    ep_id: str
    score: float
    category: str   # "Compliant" | "Partial" | "Non-Compliant"


class MatrixResponse(BaseModel):
    requirements: List[RequirementNode]
    endpoints: List[EndpointNode]
    cells: List[MatrixCell]
    overall_compliance: float
    graph_nodes: List[Dict[str, Any]]   # For 3D force graph
    graph_edges: List[Dict[str, Any]]


# --- Drift Response ---

class DriftPair(BaseModel):
    requirement_id: str
    requirement_title: str
    endpoint_id: str
    score_v1: float
    score_v2: float
    delta: float
    severity: str
    drifted: bool
    endpoint_status: str
    llm_explanation: Optional[str] = None


class DriftResponse(BaseModel):
    pairs: List[DriftPair]
    compliance_v1: float
    compliance_v2: float
    n_drifted: int
    n_critical: int
    n_major: int


# --- PR Review Response ---

class PRViolation(BaseModel):
    requirement_id: str
    requirement_text: str = ""
    endpoint_id: str
    change_status: str = "modified"          # added | removed | modified
    score_v1: float
    score_v2: float
    delta: float
    severity: str
    endpoint_change_description: str = ""
    explanation: str = ""


class PRReviewResponse(BaseModel):
    scenario: str
    verdict: str                             # PASS | WARN | BLOCK
    approved: bool
    summary: str = ""
    violations: List[PRViolation]
    changes: List[Dict[str, Any]] = []
    review_comment: str   # GitHub-style markdown


# --- Benchmark Response ---

class ModelMetrics(BaseModel):
    model_name: str
    precision: float
    recall: float
    f1: float
    map: float


class BenchmarkResponse(BaseModel):
    threshold: float
    transformer: ModelMetrics
    tfidf: ModelMetrics
    per_requirement: List[Dict[str, Any]]
