"""Strict generated-output schemas and host-owned request metadata."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .workflow import WorkflowError


class StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class Shot(StrictModel):
    shot_id: str = Field(min_length=1)
    order: int = Field(ge=1)
    duration_ms: int = Field(gt=0)
    narration: str = Field(min_length=1)
    visual_prompt: str = Field(min_length=1)
    visual_type: Literal["技术动画", "生成视频", "导入素材"]


class Document(StrictModel):
    title: str = Field(min_length=1)
    script: str = Field(min_length=1)
    shots: list[Shot] = Field(min_length=1)


class Candidate(StrictModel):
    candidate_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    angle: str = Field(min_length=1)
    suggested_duration_ms: int = Field(gt=0)
    reason: str = ""
    outline: list[str] = Field(default_factory=list, max_length=5)


class Topics(StrictModel):
    candidates: list[Candidate] = Field(min_length=1, max_length=8)


class Issue(StrictModel):
    problem: str
    fix: str
    severity: Literal["info", "warning", "error"]
    blocking: bool


class Review(StrictModel):
    verdict: Literal["approve", "revise", "reject"]
    issues: list[Issue]
    revised_document: Document


class Envelope(StrictModel):
    task_id: str
    run_id: str
    stage: str
    upstream_sha256: str
    data: dict


SCHEMAS = {"topic": Topics, "draft": Document, "review": Review, "final": Document, "storyboard": Document}


@dataclass(frozen=True)
class TextRequest:
    task_id: str
    project_id: str
    step_id: str
    run_id: str
    stage: str
    provider: str
    model_requested: str | None
    upstream_sha256: str
    input_payload: dict
    upstream_artifact_ids: tuple[str, ...]
    expected_latest: str
    prompt: str
    evidence_dir: Path
    cancel: Event
    total_timeout_ms: int = 210000


@dataclass(frozen=True)
class TextResult:
    raw_text: str
    provider_ref: dict
    model_observed: str | None = None


class ProviderError(WorkflowError):
    def __init__(self, code, message, *, dispatched=False, provider_ref=None, retryable=False):
        super().__init__(code, message)
        self.dispatched = dispatched
        self.provider_ref = provider_ref or {}
        self.retryable = retryable


def provider_failure(message: str, *, dispatched=False, provider_ref=None):
    lower = message.lower()
    if any(term in lower for term in ("quota", "rate limit", "resource_exhausted", "429")):
        code, retryable = "QUOTA_EXHAUSTED", False
    elif any(term in lower for term in ("no capacity", "503", "temporarily unavailable", "unavailable (code")):
        code, retryable = "PROVIDER_UNAVAILABLE", True
    elif any(term in lower for term in ("authentication required", "not authenticated", "unauthorized", "not logged in")):
        code, retryable = "AUTH_REQUIRED", False
    else:
        code, retryable = "CLI_STATUS_ERROR", False
    return ProviderError(code, message, dispatched=dispatched, provider_ref=provider_ref, retryable=retryable)


def output_schema(stage: str) -> dict:
    schema = Envelope.model_json_schema()
    data = SCHEMAS[stage].model_json_schema()
    schema.setdefault("$defs", {}).update(data.pop("$defs", {}))
    schema["properties"]["data"] = data
    return schema


def validate_generated(request: TextRequest, data: dict) -> dict:
    try:
        envelope = Envelope.model_validate(data)
        for name in ("task_id", "run_id", "stage", "upstream_sha256"):
            if getattr(envelope, name) != getattr(request, name):
                raise WorkflowError("INPUT_VERSION_MISMATCH", f"返回的 {name} 与当前执行不一致。")
        payload = SCHEMAS[request.stage].model_validate(envelope.data).model_dump()
    except ValidationError as error:
        raise WorkflowError("SCHEMA_MISMATCH", str(error)) from error
    if request.stage == "topic":
        ids = [c["candidate_id"] for c in payload["candidates"]]
        if len(set(ids)) != len(ids):
            raise WorkflowError("CANDIDATE_ID", "选题候选 ID 重复。")
        return payload
    document = payload["revised_document"] if request.stage == "review" else payload
    shots = document["shots"]
    if len({s["shot_id"] for s in shots}) != len(shots):
        raise WorkflowError("SHOT_ID", "分镜 ID 重复。")
    if [s["order"] for s in shots] != list(range(1, len(shots) + 1)):
        raise WorkflowError("SHOT_ORDER", "分镜顺序必须从 1 连续递增。")
    brief = request.input_payload["brief"]
    if sum(s["duration_ms"] for s in shots) != brief["target_ms"]:
        raise WorkflowError("SHOT_TOTAL", "生成分镜的总时长与项目目标不一致。")
    for text in [document["title"], document["script"], *(s["visual_prompt"] for s in shots), *(s["narration"] for s in shots)]:
        if not text.strip():
            raise WorkflowError("EMPTY_RESPONSE", "生成内容包含空白正文或镜头描述。")
    if request.stage == "review":
        # Preserve the review verdict and original structured document while
        # exposing the full corrected text in the same editor as local versions.
        return {**document, **payload, "notes": "\n".join(f"{i['problem']} → {i['fix']}" for i in payload["issues"])}
    return document
