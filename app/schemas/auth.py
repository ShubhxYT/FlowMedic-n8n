from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)
    password: SecretStr = Field(min_length=12, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value):
        value = value.strip().casefold()
        if value.count("@") != 1 or any(c.isspace() for c in value):
            raise ValueError("Invalid email")
        local, domain = value.split("@")
        if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
            raise ValueError("Invalid email")
        return value


class WorkspaceSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str = Field(min_length=36, max_length=36)


class InviteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["admin", "viewer"] = "viewer"


class InviteAccept(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: SecretStr = Field(min_length=40, max_length=128)


class MemberUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["admin", "viewer"]
