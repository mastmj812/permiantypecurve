from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(
        default="postgresql+psycopg://permian:permian@localhost:5432/permian",
        alias="DATABASE_URL",
    )
    jwt_secret: str = Field(default="dev-only-change-me", alias="JWT_SECRET")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    sentry_dsn: str | None = Field(default=None, alias="SENTRY_DSN")

    pmtiles_path: Path = Field(
        default=Path("/app/basemap/permian.pmtiles"), alias="PMTILES_PATH"
    )
    plss_geojson_path: Path = Field(
        default=Path("/app/basemap/plss_tx_nm.geojson"), alias="PLSS_GEOJSON_PATH"
    )

    enverus_api_key_prism: str | None = Field(default=None, alias="ENVERUS_API_KEY_PRISM")
    enverus_api_key_di: str | None = Field(default=None, alias="ENVERUS_API_KEY_DI")


settings = Settings()
