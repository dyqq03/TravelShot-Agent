import json
import re
import base64
import binascii
from typing import Any

from pydantic import BaseModel, Field, field_validator


MAX_REFERENCE_IMAGE_CHARS = 3_000_000
MAX_REFERENCE_IMAGES_TOTAL_CHARS = 6_000_000
MAX_REFERENCE_IMAGE_BYTES = 2_250_000
MAX_REFERENCE_IMAGES_TOTAL_BYTES = 4_500_000
MAX_LOCATION_JSON_CHARS = 2_000
REFERENCE_IMAGE_PREFIX_RE = re.compile(r"^data:image/(png|jpe?g|webp);base64,", re.IGNORECASE)


def _detect_image_type(payload: bytes) -> str | None:
    if payload.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if len(payload) >= 12 and payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return "webp"
    return None


def _validate_reference_images(images: list[str]) -> list[str]:
    total_chars = 0
    total_bytes = 0
    for index, image in enumerate(images, start=1):
        if not isinstance(image, str):
            raise ValueError(f"reference_images[{index}] must be a string.")
        total_chars += len(image)
        if len(image) > MAX_REFERENCE_IMAGE_CHARS:
            raise ValueError(f"reference_images[{index}] is too large.")
        match = REFERENCE_IMAGE_PREFIX_RE.match(image)
        if not match:
            raise ValueError(f"reference_images[{index}] must be a png/jpeg/webp data URL.")
        base64_payload = image[match.end():]
        if not base64_payload:
            raise ValueError(f"reference_images[{index}] has invalid base64 data.")
        try:
            decoded = base64.b64decode(base64_payload, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError(f"reference_images[{index}] has invalid base64 data.") from exc
        if len(decoded) > MAX_REFERENCE_IMAGE_BYTES:
            raise ValueError(f"reference_images[{index}] decoded image is too large.")
        declared_type = match.group(1).lower().replace("jpg", "jpeg")
        actual_type = _detect_image_type(decoded)
        if actual_type is None or actual_type != declared_type:
            raise ValueError(f"reference_images[{index}] file content does not match its image type.")
        total_bytes += len(decoded)
    if total_chars > MAX_REFERENCE_IMAGES_TOTAL_CHARS:
        raise ValueError("reference_images total size is too large.")
    if total_bytes > MAX_REFERENCE_IMAGES_TOTAL_BYTES:
        raise ValueError("reference_images total decoded size is too large.")
    return images


def _validate_location(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    serialized = json.dumps(value, ensure_ascii=False, default=str)
    if len(serialized) > MAX_LOCATION_JSON_CHARS:
        raise ValueError("current_location is too large.")
    return value


class PlanCreateRequest(BaseModel):
    user_input: str = Field(min_length=1, max_length=2000)
    reference_images: list[str] = Field(default_factory=list, max_length=3)

    @field_validator("reference_images")
    @classmethod
    def validate_reference_images(cls, images: list[str]) -> list[str]:
        return _validate_reference_images(images)


class PlanCreateResponse(BaseModel):
    plan_id: str
    status: str
    parsed_goal: dict[str, Any]
    warnings: list[str] = Field(default_factory=list)
    llm_used: bool = False


class PlanGenerateResponse(BaseModel):
    plan_id: str
    status: str
    parsed_goal: dict[str, Any]
    visual_goal: dict[str, Any] = Field(default_factory=dict)
    weather_context: dict[str, Any] = Field(default_factory=dict)
    sunlight_context: dict[str, Any] = Field(default_factory=dict)
    map_context: dict[str, Any] = Field(default_factory=dict)
    reference_context: dict[str, Any] = Field(default_factory=dict)
    discovery_context: dict[str, Any] = Field(default_factory=dict)
    image_analysis: dict[str, Any] = Field(default_factory=dict)
    repair_context: dict[str, Any] = Field(default_factory=dict)
    task_plan: list[dict[str, Any]] = Field(default_factory=list)
    agent_steps: list[dict[str, Any]] = Field(default_factory=list)
    final_markdown: str
    route: list[dict[str, Any]]
    spot_time_options: list[dict[str, Any]]
    backup_plan: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    llm_used: bool = False


class PlanResponse(BaseModel):
    plan_id: str
    status: str
    user_input: str
    parsed_goal: dict[str, Any]
    visual_goal: dict[str, Any] | None = None
    weather_context: dict[str, Any] | None = None
    sunlight_context: dict[str, Any] | None = None
    map_context: dict[str, Any] | None = None
    reference_context: dict[str, Any] | None = None
    discovery_context: dict[str, Any] | None = None
    image_analysis: dict[str, Any] | None = None
    repair_context: dict[str, Any] | None = None
    task_plan: list[dict[str, Any]] = Field(default_factory=list)
    agent_steps: list[dict[str, Any]] = Field(default_factory=list)
    final_markdown: str | None = None
    route: list[dict[str, Any]] = Field(default_factory=list)
    spot_time_options: list[dict[str, Any]] = Field(default_factory=list)
    backup_plan: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    llm_used: bool = False
    execution_state: dict[str, Any] | None = None
    reference_images: list[str] = Field(default_factory=list)
    created_at: Any | None = None
    updated_at: Any | None = None


class PlanSummary(BaseModel):
    plan_id: str
    status: str | None = None
    user_input: str
    destination: str | None = None
    date_range: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    llm_used: bool = False
    final_markdown: str | None = None
    created_at: Any | None = None
    updated_at: Any | None = None


class FollowUpRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    reference_images: list[str] = Field(default_factory=list, max_length=3)

    @field_validator("reference_images")
    @classmethod
    def validate_reference_images(cls, images: list[str]) -> list[str]:
        return _validate_reference_images(images)


class FollowUpResponse(BaseModel):
    plan_id: str
    status: str
    answer: str
    changes: list[dict[str, Any]] = Field(default_factory=list)
    tool_results: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    messages: list[dict[str, Any]] = Field(default_factory=list)


class ExecutionStateRequest(BaseModel):
    current_time: str | None = Field(default=None, max_length=80)
    current_location: dict[str, Any] | None = None
    user_feedback: str | None = Field(default=None, max_length=1000)

    @field_validator("current_location")
    @classmethod
    def validate_current_location(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return _validate_location(value)


class ExecutionAdjustRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)
    current_time: str | None = Field(default=None, max_length=80)
    current_location: dict[str, Any] | None = None

    @field_validator("current_location")
    @classmethod
    def validate_current_location(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return _validate_location(value)


class LiveStartResponse(BaseModel):
    plan_id: str
    status: str
    execution_state: dict[str, Any]
