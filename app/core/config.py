from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.integrations.webhook import WebhookConfigurationError, validate_webhook_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    n8n_base_url: str | None = None
    n8n_api_key: SecretStr = SecretStr("")
    database_url: str = "sqlite:///./flowmedic.db"
    request_timeout: float = Field(default=15, gt=0, le=120)
    ai_api_key: SecretStr = SecretStr("")
    ai_base_url: str = "https://api.openai.com/v1"
    ai_model: str = "gpt-4.1-mini"
    flowmedic_api_key: SecretStr = SecretStr("")
    demo_mode: bool = False
    public_demo_readonly: bool = False
    cors_allow_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    monitor_poll_interval_seconds: float = Field(default=60, ge=10, le=3600)
    monitor_page_size: int = Field(default=50, ge=1, le=100)
    monitor_backfill_pages_per_cycle: int = Field(default=1, ge=0, le=5)
    monitor_lease_seconds: float = Field(default=120, ge=30, le=3600)
    monitor_max_retries: int = Field(default=3, ge=0, le=5)
    monitor_retry_base_seconds: float = Field(default=2, gt=0, le=60)
    execution_history_retention_days: int = Field(default=30, ge=1, le=3650)
    retention_cleanup_interval_seconds: float = Field(default=86400, ge=60, le=604800)
    retention_cleanup_batch_size: int = Field(default=500, ge=1, le=10000)
    alert_default_cooldown_seconds: int = Field(default=300, ge=60, le=86400)
    alert_max_retries: int = Field(default=3, ge=1, le=5)
    alert_webhook_url: str | None = None
    alert_webhook_timeout_seconds: float = Field(default=5, gt=0, le=15)
    alert_webhook_signing_secret: SecretStr = SecretStr("")

    @field_validator("n8n_base_url", "ai_base_url")
    @classmethod
    def valid_url(cls, value):
        if value is None:
            return value
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Expected an HTTP(S) base URL without credentials, query or fragment")
        return value.rstrip("/")

    @field_validator("alert_webhook_url")
    @classmethod
    def valid_webhook_url(cls, value):
        if value is None or not value.strip():
            return None
        try:
            return validate_webhook_url(value.strip())
        except WebhookConfigurationError as exc:
            raise ValueError(str(exc)) from exc

    @model_validator(mode="after")
    def key_requires_url(self):
        if self.n8n_api_key.get_secret_value() and not self.n8n_base_url:
            raise ValueError("N8N_BASE_URL is required when N8N_API_KEY is set")
        if self.public_demo_readonly and not self.demo_mode:
            raise ValueError("PUBLIC_DEMO_READONLY requires DEMO_MODE=true")
        if self.demo_mode and (
            self.n8n_api_key.get_secret_value() or self.ai_api_key.get_secret_value()
        ):
            raise ValueError("DEMO_MODE cannot be used with N8N_API_KEY or AI_API_KEY")
        if self.monitor_lease_seconds <= self.request_timeout:
            raise ValueError("MONITOR_LEASE_SECONDS must exceed REQUEST_TIMEOUT")
        return self
