from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SUL_", env_file=".env", extra="ignore")

    artifact_root: Path = Path("artifacts")
    model_base_url: str = "http://localhost:11434"
    model_name: str = "qwen3:8b"
    model_fallback_names: str = ""
    model_concurrency: int = Field(default=1, ge=1)
    model_timeout_seconds: float = Field(default=45.0, gt=0)
    browser_origin: str = "http://127.0.0.1:8001"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    qdrant_url: str | None = None
    qdrant_api_key: str | None = None
    qdrant_collection: str = "sul_memory"
    postgres_dsn: str | None = None
    business_fault: str | None = None

    def ensure_artifact_root(self) -> Path:
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        return self.artifact_root

    def configured_model_names(self) -> list[str]:
        """Return the primary model followed by comma-separated fallbacks."""
        names = [self.model_name.strip()]
        names.extend(item.strip() for item in self.model_fallback_names.split(","))
        return list(dict.fromkeys(item for item in names if item))
