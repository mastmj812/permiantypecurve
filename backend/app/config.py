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

    pmtiles_path: Path = Field(default=Path("/app/basemap/permian.pmtiles"), alias="PMTILES_PATH")
    plss_geojson_path: Path = Field(
        default=Path("/app/basemap/plss_tx_nm.geojson"), alias="PLSS_GEOJSON_PATH"
    )
    blocks_geojson_path: Path = Field(
        default=Path("/app/basemap/blocks_tx_nm.geojson"), alias="BLOCKS_GEOJSON_PATH"
    )
    sections_geojson_path: Path = Field(
        default=Path("/app/basemap/sections_tx_nm.geojson"),
        alias="SECTIONS_GEOJSON_PATH",
    )
    basement_faults_geojson_path: Path = Field(
        default=Path("/app/basemap/faults_basement.geojson"),
        alias="BASEMENT_FAULTS_GEOJSON_PATH",
    )
    snf_faults_geojson_path: Path = Field(
        default=Path("/app/basemap/faults_snf.geojson"),
        alias="SNF_FAULTS_GEOJSON_PATH",
    )

    # Read-only DSN for engineering_db's curated.* materialized views. Powers
    # the warehouse_client data layer (replaces enverus_client post-cutover).
    # None = warehouse not configured; warehouse_client calls raise on use,
    # but the app still boots so the legacy Enverus path keeps working.
    warehouse_database_url: str | None = Field(default=None, alias="WAREHOUSE_DATABASE_URL")


settings = Settings()
