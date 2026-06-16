from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve the root .env regardless of where uvicorn is launched from.
# config.py lives at backend/app/core/config.py — parents[3] is the project root.
_ROOT_ENV = str(Path(__file__).resolve().parents[3] / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ROOT_ENV,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    APP_ENV: str = "development"
    SECRET_KEY: str = "change-me-in-production"

    # Render / production hosts can provide a single DATABASE_URL.
    # If provided, it takes precedence over the individual POSTGRES_* vars.
    DATABASE_URL: str | None = None

    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "vulcanops"
    POSTGRES_USER: str = "vulcanops"
    POSTGRES_PASSWORD: str = "vulcanops"

    REDIS_URL: str | None = None
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str = ""

    # InfluxDB can be provided as a full URL (Render / InfluxDB Cloud) or
    # built from host/port/token/bucket/org.
    INFLUX_URL: str | None = None
    INFLUXDB_HOST: str = "localhost"
    INFLUXDB_PORT: int = 8086
    INFLUXDB_ORG: str = "vulcanops"
    INFLUXDB_BUCKET: str = "telemetry"
    INFLUXDB_TOKEN: str = "change-me-in-production"

    # CORS — comma-separated list of allowed origins. FRONTEND_URL is
    # automatically appended if set and not already present.
    ALLOWED_ORIGINS: str = "http://localhost:5173"
    FRONTEND_URL: str = ""

    # OpenRouter LLM — single model used for all LLM calls
    OPENROUTER_API_KEY: str = ""
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    LLM_MODEL: str = "google/gemini-2.5-flash"
    LLM_TIMEOUT: float = 20.0

    @property
    def database_url(self) -> str:
        if self.DATABASE_URL:
            return self._to_asyncpg(self.DATABASE_URL)
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def redis_url(self) -> str:
        if self.REDIS_URL:
            return self.REDIS_URL
        if self.REDIS_PASSWORD:
            return f"redis://:{self.REDIS_PASSWORD}@{self.REDIS_HOST}:{self.REDIS_PORT}/0"
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/0"

    @property
    def influxdb_url(self) -> str:
        if self.INFLUX_URL:
            return self.INFLUX_URL
        return f"http://{self.INFLUXDB_HOST}:{self.INFLUXDB_PORT}"

    @property
    def allowed_origins_list(self) -> list[str]:
        origins = {o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()}
        if self.FRONTEND_URL and self.FRONTEND_URL.strip():
            origins.add(self.FRONTEND_URL.strip())
        return list(origins)

    @staticmethod
    def _to_asyncpg(url: str) -> str:
        """Render DATABASE_URL often starts with postgres:// or postgresql://.

        SQLAlchemy asyncpg driver requires postgresql+asyncpg://.
        """
        if url.startswith("postgres://"):
            return url.replace("postgres://", "postgresql+asyncpg://", 1)
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url


settings = Settings()
