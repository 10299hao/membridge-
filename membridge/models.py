from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MemoryType = Literal["decision", "todo", "file_change", "preference", "fact"]
ToolName = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")]


class Message(BaseModel):
    id: str
    role: Literal["user", "assistant", "tool"]
    content: str = Field(max_length=2_000_000)
    ts: str | None = None


class Session(BaseModel):
    session_id: str
    source_tool: ToolName
    project: str
    title: str = ""
    started_at: str | None = None
    messages: list[Message] = Field(min_length=1, max_length=100_000)
    metadata: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique_ids(self):
        if len({m.id for m in self.messages}) != len(self.messages):
            raise ValueError("Message IDs must be unique")
        return self


class MemoryDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: MemoryType
    text: str = Field(min_length=1, max_length=2000)
    evidence: list[str] = Field(min_length=1)
    status: Literal["active", "completed"] = "active"
    subject: str | None = Field(default=None, max_length=100)


class Extraction(BaseModel):
    memories: list[MemoryDraft] = Field(default_factory=list, max_length=500)


class ImportRequest(BaseModel):
    source_tool: ToolName = "opencode"
    project: str = Field(min_length=1, max_length=2000)
    data: dict
    refresh: bool = False
    input_format: ToolName | None = None
    repository: dict = Field(default_factory=dict)


class SearchRequest(BaseModel):
    project: str = Field(min_length=1)
    query: str = Field(default="", max_length=2000)
    limit: int = Field(default=12, ge=1, le=100)


class HandoffRequest(SearchRequest):
    max_chars: int = Field(default=6000, ge=1000, le=32000)
    source_job: str | None = None


class ResolveRequest(BaseModel):
    project: str = Field(min_length=1)
    memory_id: str
    replacement_id: str | None = None
    action: Literal["supersedes", "completed", "disputed"]


class ForgetRequest(BaseModel):
    project: str
    session_id: str | None = None
    apply: bool = False


class RetentionRequest(BaseModel):
    project: str
    days: int | None = Field(default=None, ge=0, le=36500)
    apply: bool = False


class CorrectionRequest(BaseModel):
    project: str
    memory_id: str
    text: str = Field(min_length=1, max_length=2000)
