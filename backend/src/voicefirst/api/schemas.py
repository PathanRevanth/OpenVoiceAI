"""Pydantic schemas for the REST API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# ~7 MB decoded per attachment; 24 MB across one message. Base64 expands
# 24 MiB to 32 MiB chars, so leave a little headroom for the encoder.
MAX_ATTACHMENT_CHARS = 10_000_000
MAX_TOTAL_ATTACHMENT_CHARS = 34_000_000

# Media the composer can send. Text-like files and Office documents are
# flattened to text by voicefirst.api.attachments; images/PDF/audio/video ride
# along as native model content.
_ALLOWED_MEDIA = (
    r"^(?:"
    r"image/(?:jpeg|png|gif|webp)"
    r"|application/pdf"
    r"|text/[A-Za-z0-9.+-]+"
    r"|audio/(?:wav|mpeg|mp3|ogg|flac|aiff|aac|mp4|webm)"
    r"|video/(?:mp4|webm|quicktime|x-matroska|mpeg)"
    r"|application/(?:json|xml|javascript|x-javascript|typescript|x-yaml|yaml|sql|graphql)"
    r"|application/vnd\.openxmlformats-officedocument\."
    r"(?:wordprocessingml\.document|spreadsheetml\.sheet|presentationml\.presentation)"
    r"|application/msword|application/vnd\.ms-excel|application/vnd\.ms-powerpoint"
    r")$"
)


class ChatAttachment(BaseModel):
    name: str = Field(max_length=256)
    media_type: str = Field(pattern=_ALLOWED_MEDIA)
    data: str = Field(max_length=MAX_ATTACHMENT_CHARS)  # base64
    # Client-rendered thumbnail (data URL). Kept separately so the thread can
    # show image previews without persisting the full original in the message.
    preview: str | None = Field(default=None, max_length=1_500_000)

    @model_validator(mode="after")
    def _b64_shape(self) -> "ChatAttachment":
        import base64

        try:
            base64.b64decode(self.data, validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError("attachment data must be valid base64") from exc
        if self.preview is not None and not self.preview.startswith("data:image/"):
            raise ValueError("attachment preview must be an image data URL")
        return self


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=32_000)
    conversation_id: str | None = None
    channel: Literal["chat", "voice"] = "chat"
    model: str | None = None  # optional per-request model override
    effort: Literal["minimal", "low", "medium", "high"] | None = None  # reasoning effort; None = provider default
    agent: str | None = None  # direct-to-specialist routing; None = orchestrator auto-routing
    attachments: list[ChatAttachment] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def _attachment_budget(self) -> "ChatRequest":
        total = sum(len(a.data) for a in self.attachments)
        if total > MAX_TOTAL_ATTACHMENT_CHARS:
            raise ValueError("attachments exceed the total size budget (24 MB decoded)")
        return self


class ApprovalDecision(BaseModel):
    approved: bool
    decided_by: str | None = None
    note: str | None = None


class AgentStatusUpdate(BaseModel):
    status: Literal["enabled", "disabled"]


class MCPServerUpsert(BaseModel):
    name: str
    transport: Literal["streamable-http", "sse", "stdio"] = "streamable-http"
    url: str | None = None
    command: str | None = None
    headers: dict[str, str] | None = None
    status: Literal["enabled", "disabled"] = "enabled"


class PolicyUpdate(BaseModel):
    """Runtime policy overrides (admin). Applied immediately, env vars win on restart."""

    require_approval_for_mutations: bool | None = None
    mutating_min_role: Literal["viewer", "ops", "admin"] | None = None
    redact_pii: bool | None = None


class RunEvent(BaseModel):
    """One streaming event emitted during a chat run."""

    type: Literal[
        "run.started",
        "text.delta",
        "text.done",
        "thinking.delta",
        "thinking.done",
        "tool.started",
        "tool.finished",
        "plan.proposed",
        "plan.task",
        "approval.requested",
        "run.finished",
        "run.failed",
        "usage",
        "sources",
        "followups",
        "heartbeat",
    ]
    data: dict[str, Any] = Field(default_factory=dict)


class ConversationRename(BaseModel):
    title: str = Field(min_length=1, max_length=256)


class RunFeedbackIn(BaseModel):
    rating: Literal["up", "down"]
    comment: str | None = Field(default=None, max_length=2000)


class InstructionsIn(BaseModel):
    instructions: str = Field(max_length=4000)


class PromptCreate(BaseModel):
    title: str = Field(min_length=1, max_length=128)
    body: str = Field(min_length=1, max_length=8000)


class PromptUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=128)
    body: str | None = Field(default=None, min_length=1, max_length=8000)


PROJECT_COLORS = ("slate", "black", "red", "orange", "yellow", "green", "blue", "violet", "pink")


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    icon: str = Field(default="folder", min_length=1, max_length=32)
    color: str = Field(default="slate")

    @field_validator("color")
    @classmethod
    def _check_color(cls, v: str) -> str:
        if v not in PROJECT_COLORS:
            raise ValueError(f"color must be one of {', '.join(PROJECT_COLORS)}")
        return v


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    instructions: str | None = Field(default=None, max_length=4000)
    icon: str | None = Field(default=None, min_length=1, max_length=32)
    color: str | None = Field(default=None, min_length=1, max_length=32)

    @field_validator("color")
    @classmethod
    def _check_color(cls, v: str | None) -> str | None:
        if v is not None and v not in PROJECT_COLORS:
            raise ValueError(f"color must be one of {', '.join(PROJECT_COLORS)}")
        return v


class ConversationMove(BaseModel):
    project_id: str | None = None
