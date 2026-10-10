from datetime import datetime
import math
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResearchRequest(StrictModel):
    question: str
    depth: Literal["brief", "deep"] = "deep"
    source_ids: list[str] = Field(default_factory=list, max_length=20)
    reader: str = Field(default="普通读者", max_length=500)
    scope: str = Field(default="", max_length=2000)
    enable_3d: bool = False

    @field_validator("question")
    @classmethod
    def normalized_question(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value.encode("utf-16-le")) // 2 > 1000:
            raise ValueError("question must contain 1 to 1000 UTF-16 characters")
        return value


class Chapter(StrictModel):
    id: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    title: str = Field(min_length=1, max_length=300)
    paragraphs: list[str] = Field(default_factory=list, max_length=2000)


class ChartPoint(StrictModel):
    label: str = Field(min_length=1, max_length=200)
    value: float

    @field_validator("value")
    @classmethod
    def finite_value(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("chart value must be finite")
        return value


class ChartMeta(StrictModel):
    title: str = Field(min_length=1, max_length=300)
    unit: str = Field(min_length=1, max_length=100)
    note: str = Field(min_length=1, max_length=2000)


class Report(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    source: Literal["generated", "import", "demo"] = "generated"
    title: str = Field(min_length=1, max_length=300)
    question: str = Field(max_length=1000)
    category: str = Field(min_length=1, max_length=100)
    summary: str = Field(min_length=1, max_length=10000)
    createdAt: str
    minutes: int = Field(ge=1, le=10000)
    chapters: list[Chapter] = Field(min_length=1, max_length=2000)
    chart: list[ChartPoint] = Field(default_factory=list, max_length=50)
    chartMeta: ChartMeta | None = None
    bookmarks: list[str] = Field(default_factory=list, max_length=2000)
    version: int = Field(default=1, ge=1)
    workflow: dict[str, Any] | None = None

    @field_validator("createdAt")
    @classmethod
    def valid_date(cls, value: str) -> str:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value

    @field_validator("workflow")
    @classmethod
    def finite_workflow(cls, value):
        def walk(item, depth=0):
            if depth > 60:
                raise ValueError("workflow nesting exceeds JSON processing limit")
            if isinstance(item, float) and not math.isfinite(item):
                raise ValueError("workflow values must be finite")
            if isinstance(item, dict):
                for child in item.values():
                    walk(child, depth + 1)
            elif isinstance(item, list):
                for child in item:
                    walk(child, depth + 1)
        walk(value)
        return value

    @model_validator(mode="after")
    def coherent(self) -> "Report":
        ids = [c.id for c in self.chapters]
        if len(set(ids)) != len(ids):
            raise ValueError("chapter ids must be unique")
        if len(set(self.bookmarks)) != len(self.bookmarks) or not set(self.bookmarks) <= set(ids):
            raise ValueError("bookmarks must reference unique existing chapters")
        if self.chart and self.chartMeta is None:
            raise ValueError("chartMeta is required for a chart")
        if sum(len(p) for c in self.chapters for p in c.paragraphs) > 500000:
            raise ValueError("report text exceeds limit")
        return self


class BookmarkRequest(StrictModel):
    bookmarks: list[str] = Field(max_length=2000)


class TaskError(StrictModel):
    code: str
    message: str


class ErrorEnvelope(StrictModel):
    error: TaskError


class Health(StrictModel):
    status: Literal["ok"]
    ready: bool
    mode: Literal["test", "production"]
    version: str
    runtime: dict[str, Any] = Field(default_factory=dict)


class ReportList(StrictModel):
    reports: list[Report]


class Task(StrictModel):
    id: str
    status: Literal["queued", "running", "paused", "succeeded", "failed", "cancelled"]
    step: int = Field(ge=0, le=3)
    message: str
    report: Report | None
    error: TaskError | None
    expiresAt: str | None


class SourceUpload(StrictModel):
    name: str = Field(min_length=1, max_length=300)
    media_type: str = Field(default="application/octet-stream", max_length=150)
    content_base64: str = Field(min_length=1, max_length=28_000_000)


class TaskAction(StrictModel):
    action: Literal["pause", "resume", "retry"]
    from_stage: Literal["A", "B", "C", "D"] | None = None


class VersionRequest(StrictModel):
    base_version: int = Field(ge=1)
    report: Report


class ReportAction(StrictModel):
    action: Literal["validate", "review", "human-review", "publish", "revoke-share", "restore", "calculate"]
    version: int | None = Field(default=None, ge=1)
    base_version: int | None = Field(default=None, ge=1)
    notes: str = Field(default="", max_length=10000)
    reviewer: str = Field(default="", max_length=200)
    decision: str = Field(default="reviewed", max_length=100)
    token: str = Field(default="", max_length=200)
    ttl_seconds: int = Field(default=7 * 86400, ge=60, le=365 * 86400)
    operation: Literal["sum", "mean", "min", "max", "difference", "ratio", "percent_change"] = "sum"
    input_refs: list[str] = Field(default_factory=list, max_length=100)
