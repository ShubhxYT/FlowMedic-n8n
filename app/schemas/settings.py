from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


class ConnectionWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    name: str | None = Field(default=None, min_length=1, max_length=120)
    base_url: str | None = Field(default=None, min_length=1, max_length=2048)
    api_key: SecretStr | None = None
    remove_api_key: bool = False
    enabled: bool | None = None
    kind: Literal["openai-compatible", "mock"] | None = None
    model: str | None = Field(default=None, min_length=1, max_length=200)
    confirm_same_server: bool = False
    make_default: bool = False
    expected_state_revision: int | None = Field(default=None, ge=1)

    @field_validator("name", "model")
    @classmethod
    def trimmed_text(cls, value):
        if value is not None and (not value.strip() or value != value.strip()):
            raise ValueError("Use nonblank text without surrounding whitespace")
        return value

    @field_validator("api_key")
    @classmethod
    def valid_secret(cls, value):
        if value is None:
            return value
        raw = value.get_secret_value()
        if not 1 <= len(raw) <= 8192 or raw != raw.strip():
            raise ValueError("Invalid API key")
        if raw.lower() in {"redacted", "[redacted]", "[stored]", "keep"}:
            raise ValueError("Supply a replacement key or omit it")
        if set(raw) <= {"*", "•", "●"}:
            raise ValueError("Masked values are not replacement keys")
        return value

    @model_validator(mode="after")
    def key_action(self):
        if "api_key" in self.model_fields_set and self.api_key is None:
            raise ValueError("Omit the key to keep it; use remove_api_key to clear it")
        if self.api_key is not None and self.remove_api_key:
            raise ValueError("Replacement and removal cannot be combined")
        if self.make_default and self.expected_state_revision is None:
            raise ValueError("Default selection requires the current workspace revision")
        return self


class ConnectionCreate(ConnectionWrite):
    name: str = Field(min_length=1, max_length=120)


class ConnectionPatch(ConnectionWrite):
    expected_revision: int = Field(ge=1)


class CandidateTest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    candidate: ConnectionCreate
    mode: Literal["access", "structured_output"] = "access"
    acknowledge_usage_cost: bool = False


class SavedTest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    expected_revision: int = Field(ge=1)
    changes: ConnectionWrite = Field(default_factory=ConnectionWrite)
    mode: Literal["access", "structured_output"] = "access"
    acknowledge_usage_cost: bool = False


class ConnectionOut(BaseModel):
    id: str
    name: str
    base_url: str | None
    kind: Literal["n8n", "openai-compatible", "mock"]
    model: str | None
    enabled: bool
    revision: int
    has_api_key: bool
    is_default: bool
    created_at: datetime
    updated_at: datetime


class ConnectionPage(BaseModel):
    items: list[ConnectionOut]
    state_revision: int


class ConnectionTestResult(BaseModel):
    ok: bool
    capability: Literal["workflows_read", "model_access", "structured_output", "mock"]
    tested_revision: int | None
    message: str
