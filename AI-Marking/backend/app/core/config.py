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

    # 网页实验中心固定读取的正式研究数据。API 不接受任意服务器文件路径。
    EXPERIMENT_DATASET_PATH: str = str(
        SURF_ROOT / "data" / "processed" / "jorgpt_experiment.csv"
    )
    R20_SAF_ARCHIVE_PATH: str = str(SURF_ROOT / "data" / "SAF2_0.zip")
    R20_SAF_SPLIT_MAP_PATH: str = str(SURF_ROOT / "data" / "saf_hf_split_map.csv")
    # r23 accepts no arbitrary path from the API; only this local restricted root.
    R23_DRESS_ROOT: str = str(SURF_ROOT / "DREsS")
    # Unified experiment core: the registered restricted dataset root. The API
    # never accepts arbitrary paths; adapters resolve dataset keys under it.
    EXP_DATASETS_ROOT: str = str(SURF_ROOT / "DREsS")
    # The r23 CASE stack is frozen history after the legacy import; mutations
    # are refused at the API boundary (new studies run on /api/experiments).
    R23_READ_ONLY: bool = True
    # Legacy r20 archival modules retain this setting for import compatibility;
    # r21 does not start or use the r20 worker.
    R20_AUTO_RETRY_SECONDS: int = 60
    R20_TIMEOUT_SECONDS: float | None = None

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
