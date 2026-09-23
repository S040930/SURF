"""实验系统应用配置。"""

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

SURF_ROOT = Path(__file__).resolve().parents[4]


def require_experiment_database_url(value: str) -> str:
    """Reject every database URL outside the isolated experiment namespace."""
    database = make_url(value.replace("%%", "%")).database or ""
    if database != "ai_marking_experiment" and not database.startswith(
        "ai_marking_experiment_test"
    ):
        raise ValueError(
            "experiment database URL must target ai_marking_experiment or an "
            "ai_marking_experiment_test* database, never the product database"
        )
    return value


class Settings(BaseSettings):
    """只声明实验运行所需的非密钥配置。"""

    DATABASE_URL: str = (
        "postgresql+psycopg2://postgres:postgres@localhost:5432/ai_marking_experiment"
    )
    CORS_ORIGINS: list[str] = ["http://localhost:5173"]

    # Independent SAF 2.0 memory-framework study.  The API resolves this
    # fixed archive only; callers cannot provide arbitrary server paths.
    MEMORY_STUDY_ARCHIVE_PATH: str = str(SURF_ROOT / "data" / "SAF2_0.zip")
    MEMORY_STUDY_ARTIFACT_ROOT: str = str(SURF_ROOT / "outputs" / "saf_memory_study")
    MEMORY_STUDY_EMBEDDING_MODEL: str = "doubao-embedding-vision"
    # A real run must set this to an immutable provider model revision.  The
    # default intentionally fails the run gate instead of silently using main.
    MEMORY_STUDY_EMBEDDING_REVISION: str = "unresolved"
    # Every study retriever uses one OpenAI-compatible /v1/embeddings endpoint.
    # The key here is only a fallback; the site-config row wins once saved.
    MEMORY_STUDY_EMBEDDING_BACKEND: str = "openai"
    MEMORY_STUDY_EMBEDDING_API_BASE: str = ""
    MEMORY_STUDY_EMBEDDING_API_KEY: str = ""
    MEMORY_STUDY_EMBEDDING_DIMS: int | None = None

    @field_validator("DATABASE_URL")
    @classmethod
    def reject_product_database(cls, value: str) -> str:
        return require_experiment_database_url(value)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


settings = Settings()
