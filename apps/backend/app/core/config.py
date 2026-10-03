"""Application settings, loaded from environment variables (prefix ``SPASHTA_``)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_UID_SALT = "dev-only-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SPASHTA_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"

    # --- HTTP -----------------------------------------------------------------
    cors_origins: list[str] = ["http://localhost:5173"]
    # Public URL of the web viewer (used for SMART redirects and CDS card links).
    frontend_url: str = "http://localhost:5173"
    # Public URL of this API as seen by an EHR (used in CDS card links).
    public_api_url: str = "http://localhost:8000"
    max_upload_bytes: int = 256 * 1024 * 1024

    # --- Persistence ----------------------------------------------------------
    # SQLite by default; use postgresql+asyncpg://user:pass@host/db in production.
    database_url: str = "sqlite+aiosqlite:///./data/spashtarad.db"
    data_dir: Path = Path("./data")

    # --- PACS (DICOMweb) ------------------------------------------------------
    dicomweb_url: str = "http://localhost:8042/dicom-web"
    dicomweb_username: str | None = "spashtarad"
    dicomweb_password: SecretStr | None = SecretStr("spashtarad")
    dicomweb_timeout_seconds: float = 60.0

    # --- Authentication -------------------------------------------------------
    # "dev" trusts every request as a local developer and is refused in production.
    auth_mode: Literal["dev", "oidc"] = "dev"
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None
    oidc_roles_claim: str = "roles"

    # --- SMART on FHIR --------------------------------------------------------
    smart_client_id: str = "spashtarad-dev"
    # Must be registered with the EHR. In development this goes through the
    # Vite proxy so the session cookie lands on the viewer's origin.
    smart_redirect_uri: str = "http://localhost:5173/api/v1/fhir/callback"
    smart_scopes: str = "openid fhirUser patient/Patient.read patient/Encounter.read"
    # Only these FHIR servers may initiate a launch (prevents SSRF / token theft
    # through an attacker-supplied `iss`).
    smart_allowed_issuers: list[str] = ["https://launch.smarthealthit.org/v/r4/fhir"]

    # --- De-identification ----------------------------------------------------
    # Secret key for deterministic UID remapping. Rotate it and previously
    # de-identified studies can no longer be linked to new ones.
    deid_uid_salt: SecretStr = SecretStr(_DEV_UID_SALT)

    # --- AI engine ------------------------------------------------------------
    ai_device: str = "cpu"
    ai_positive_threshold: float = 0.5
    ai_max_heatmaps: int = 5
    ai_msk_weights_path: Path | None = None
    ai_enable_zero_shot: bool = True

    @model_validator(mode="after")
    def _refuse_insecure_production(self) -> Settings:
        if self.environment != "production":
            return self
        problems = []
        if self.auth_mode == "dev":
            problems.append("SPASHTA_AUTH_MODE=dev is not allowed")
        if self.deid_uid_salt.get_secret_value() == _DEV_UID_SALT:
            problems.append("SPASHTA_DEID_UID_SALT must be set to a private value")
        if any(origin == "*" for origin in self.cors_origins):
            problems.append("SPASHTA_CORS_ORIGINS must not contain '*'")
        if problems:
            raise ValueError("Insecure production configuration: " + "; ".join(problems))
        return self

    @property
    def heatmap_dir(self) -> Path:
        return self.data_dir / "heatmaps"


@lru_cache
def get_settings() -> Settings:
    return Settings()
