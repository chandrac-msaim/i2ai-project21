"""Settings from the repo-root .env (see .env.example)."""
from datetime import date
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    database_url: str = "postgresql+psycopg://graydi:graydi_dev_pw@localhost:5432/graydi"
    jwt_secret: str = "change-me-dev-only"
    jwt_expire_minutes: int = 480
    llm_provider: str = "ollama"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:3b"
    anthropic_api_key: str | None = None
    claude_model: str = "claude-haiku-4-5"
    llm_timeout_s: float = 60.0
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"  # 384 dims = courses.embedding
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    mail_from: str = "advising@demo.graydi.local"
    app_env: str = "production"  # APP_ENV: "development" shows the demo logins on the login page
    frontend_url: str = "http://localhost:5173"  # FRONTEND_URL: the link in the decision email
    app_today: date | None = None  # APP_TODAY; empty = real date

    @field_validator("app_today", mode="before")
    @classmethod
    def _empty_is_none(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    def today(self) -> date:
        """The app's "today": APP_TODAY if set, else the real date."""
        return self.app_today or date.today()


@lru_cache
def get_settings() -> Settings:
    return Settings()
