from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SUL_", env_file=".env", extra="ignore")

    artifact_root: Path = Path("artifacts")
    model_base_url: str = "http://localhost:11434"
    model_name: str = "qwen3:8b"
    model_concurrency: int = Field(default=1, ge=1)
    browser_origin: str = "http://127.0.0.1:8001"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    oracle_dsn: str | None = None
    oracle_user: str | None = None
    oracle_password: str | None = None

    def ensure_artifact_root(self) -> Path:
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        return self.artifact_root
