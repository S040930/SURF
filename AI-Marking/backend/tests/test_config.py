import pytest
from pydantic import ValidationError

from app.core.config import Settings, require_experiment_database_url


def test_settings_reject_product_database():
    with pytest.raises(ValidationError, match="never the product database"):
        Settings(
            DATABASE_URL=(
                "postgresql+psycopg2://postgres:postgres@localhost:5432/ai_marking"
            ),
            _env_file=None,
        )


def test_settings_accept_experiment_database():
    configured = Settings(
        DATABASE_URL=(
            "postgresql+psycopg2://postgres:postgres@localhost:5432/"
            "ai_marking_experiment"
        ),
        _env_file=None,
    )
    assert configured.DATABASE_URL.endswith("/ai_marking_experiment")


def test_explicit_migration_url_uses_the_same_database_guard():
    assert require_experiment_database_url(
        "postgresql://localhost/ai_marking_experiment_test_migration"
    ).endswith("ai_marking_experiment_test_migration")
    with pytest.raises(ValueError, match="never the product database"):
        require_experiment_database_url("postgresql://localhost/ai_marking")
    with pytest.raises(ValueError, match="never the product database"):
        require_experiment_database_url(
            "postgresql://localhost/ai_marking_experiment_backup"
        )
