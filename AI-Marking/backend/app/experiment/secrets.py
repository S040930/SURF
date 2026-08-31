"""本地实验密钥存储；密钥永不进入数据库或 HTTP 响应。"""

from __future__ import annotations

import os
import re
from pathlib import Path

from dotenv import dotenv_values, set_key, unset_key

from app.core.config import settings


def secrets_path() -> Path:
    path = Path(settings.EXPERIMENT_SECRETS_FILE)
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[2] / path
    return path


def get_secret_by_name(name: str) -> str:
    values = dotenv_values(secrets_path()) if secrets_path().exists() else {}
    file_value = values.get(name)
    if isinstance(file_value, str) and file_value:
        return file_value
    return os.getenv(name, "")


def set_secret_by_name(name: str, value: str) -> None:
    """Store a local experiment secret without placing it in the database."""
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
        raise ValueError(
            "secret variable name must contain only uppercase letters, digits, and underscores"
        )
    path = secrets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    set_key(str(path), name, value, quote_mode="auto")
    path.chmod(0o600)
    os.environ[name] = value


def delete_secret_by_name(name: str) -> None:
    """Delete only keys generated for experiment model configurations."""
    if not name.startswith("EXPERIMENT_MODEL_API_KEY_") or not re.fullmatch(
        r"[A-Z][A-Z0-9_]*", name
    ):
        return
    path = secrets_path()
    if path.exists():
        unset_key(str(path), name)
    os.environ.pop(name, None)
