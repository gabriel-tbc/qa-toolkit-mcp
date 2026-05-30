"""Pydantic models mirroring run-report.v1.json (schema versions 1.0 and 1.1).

These models are the in-memory representation of a run report after it has been
read from disk. They are the single source of truth used by storage, compare,
formatters, and the MCP tools.

v1.1 adds two optional fields without breaking v1.0 consumers:
- `TestCase.classification` (Classification enum, optional)
- `RunReport.is_exhaustive` (bool, default True)
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class TestStatus(str, Enum):
    __test__ = False  # pytest: do not collect this enum as a test class

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"


class Classification(str, Enum):
    """QA-assigned classification: the catalog of outcome labels a reviewer can assign."""

    UNCLASSIFIED = "unclassified"
    POSITIVE_FUNCTIONAL = "positive-functional"
    VALIDATION_EXPECTED = "validation-expected"
    SCHEMA_STRICTNESS_CANDIDATE = "schema-strictness-candidate"
    BUSINESS_RULE_CANDIDATE = "business-rule-candidate"
    INTEGRATION_GAP_OR_PROVIDER_ERROR = "integration-gap-or-provider-error"
    BUG_REAL = "bug real"
    MANUAL_REVIEW_PENDING = "manual-review: pendiente usuario"


class TestError(BaseModel):
    __test__ = False  # pytest: not a test class
    model_config = ConfigDict(extra="forbid")

    type: str = Field(..., description="Exception or failure type (e.g., 'AssertionError').")
    message: str = Field(..., description="Raw failure message. May contain volatile data.")
    fingerprint: str = Field(
        ...,
        min_length=8,
        description=(
            "Hash of (test_id, error.type, normalized_message). Two failures "
            "with the same fingerprint are treated as 'same error' by compare_runs."
        ),
    )


class TestCase(BaseModel):
    __test__ = False  # pytest: not a test class
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, description="Stable test identifier across runs.")
    name: Optional[str] = Field(default=None, description="Human-readable name; falls back to id.")
    status: TestStatus
    duration_ms: Optional[float] = Field(default=None, ge=0)
    error: Optional[TestError] = None
    tags: list[str] = Field(default_factory=list)
    classification: Optional[Classification] = Field(
        default=None,
        description=(
            "v1.1+. Human-assigned classification of the test outcome. The QA "
            "engineer edits this post-run; the MCP only reads it."
        ),
    )


class GitInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    commit: Optional[str] = None
    branch: Optional[str] = None


class RunSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int = Field(..., ge=0)
    passed: int = Field(..., ge=0)
    failed: int = Field(..., ge=0)
    skipped: int = Field(..., ge=0)
    errors: int = Field(..., ge=0)


class RunReport(BaseModel):
    """Top-level model. Mirror of run-report.v1.json (versions 1.0 and 1.1)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(..., pattern=r"^1\.[01]$")
    run_id: str = Field(..., min_length=1)
    suite: str = Field(..., min_length=1)
    started_at: datetime
    finished_at: Optional[datetime] = None
    is_exhaustive: bool = Field(
        default=True,
        description=(
            "v1.1+. True iff `tests[]` lists every test that ran. False when "
            "the report only contains failures (e.g., a classification.json "
            "without a JUnit XML companion)."
        ),
    )
    git: Optional[GitInfo] = None
    env: dict[str, str | int | float | bool] = Field(default_factory=dict)
    summary: RunSummary
    tests: list[TestCase] = Field(default_factory=list)
