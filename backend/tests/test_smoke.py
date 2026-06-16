"""Deployment smoke tests — no external services required."""

import os


def test_database_url_conversion():
    """Render provides postgres:// URLs; config must convert them to asyncpg."""
    from app.core.config import Settings

    s = Settings(
        DATABASE_URL="postgres://user:pass@render-host:5432/dbname",
        APP_ENV="production",
    )
    assert s.database_url == "postgresql+asyncpg://user:pass@render-host:5432/dbname"


def test_database_url_strips_sslmode_for_asyncpg():
    """asyncpg does not accept sslmode as a connect keyword."""
    from app.core.config import Settings

    s = Settings(
        DATABASE_URL="postgresql://user:pass@render-host:5432/dbname?sslmode=require",
        APP_ENV="production",
    )
    assert s.database_url == "postgresql+asyncpg://user:pass@render-host:5432/dbname"
    assert s.database_connect_args == {"ssl": "require"}


def test_allowed_origins_includes_frontend_url():
    from app.core.config import Settings

    s = Settings(
        ALLOWED_ORIGINS="http://localhost:5173",
        FRONTEND_URL="https://app.vercel.app",
    )
    assert "http://localhost:5173" in s.allowed_origins_list
    assert "https://app.vercel.app" in s.allowed_origins_list


def test_fastapi_app_imports():
    """The app factory must import without crashing."""
    from app.main import app

    assert app.title == "VulcanOps"
    paths = {route.path for route in app.routes}
    assert "/health" in paths
    assert "/ready" in paths
