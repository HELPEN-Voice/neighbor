# src/ii_agent/tools/neighbor/config/settings.py
import os
from pathlib import Path

try:
    from pydantic_settings import BaseSettings
except ImportError:
    # Fallback for Pydantic v1
    from pydantic import BaseSettings

from pydantic import Field
from typing import Literal, Optional

# .env is at repo root ~/neighbor/.env
# This file: ~/neighbor/src/neighbor/config/settings.py (4 levels deep)
_ENV_FILE = Path(__file__).parent.parent.parent.parent / ".env"


class NeighborSettings(BaseSettings):
    # Keys
    OPENAI_API_KEY: str | None = Field(default=None, env="OPENAI_API_KEY")
    REGRID_API_KEY: str | None = Field(default=None, env="REGRID_API_KEY")

    # Engine selection: start with responses; switch to agentsdk later with no code changes elsewhere
    ENGINE_TYPE: Literal["responses", "agentsdk"] = "responses"

    # Deep Research model selection
    DR_MODEL: Literal[
        "o4-mini-deep-research-2025-06-26", "o3-deep-research-2025-06-26"
    ] = "o3-deep-research-2025-06-26"

    # Concurrency & batching
    BATCH_SIZE: int = 3
    MAX_NEIGHBORS: int = 30  # Max owners to return
    MAX_PARCELS: int = 30  # Hard cap on parcels fetched from Regrid API (billing optimization)
    DEFAULT_RADIUS_MILES: float = 0.25  # Starting radius for expansion (doubles each iteration)
    CONCURRENCY_LIMIT: int = 15  # guardrail if you want to cap in dense areas

    # Future toggles (not used yet; here for forward-compat)
    STREAMING_ENABLED: bool = False
    TRACE_ENABLED: bool = False

    # Map Generation Settings
    GENERATE_MAP: bool = Field(
        default=True, description="Enable map generation after neighbor screening"
    )
    MAPBOX_ACCESS_TOKEN: str = Field(
        default="", env="MAPBOX_ACCESS_TOKEN", description="Mapbox public access token"
    )
    MAPBOX_STYLE: str = Field(
        default="satellite-streets-v12", description="Mapbox style ID"
    )
    MAP_WIDTH: int = Field(default=800, description="Map image width in pixels")
    MAP_HEIGHT: int = Field(
        default=450, description="Map image height in pixels (16:9 ratio)"
    )
    MAP_PADDING: int = Field(
        default=50, description="Padding around features in pixels"
    )
    MAP_RETINA: bool = Field(default=True, description="Generate @2x retina images")

    # Geometry Processing
    SIMPLIFY_TOLERANCE: float = Field(
        default=0.0001, description="Douglas-Peucker simplification tolerance (~10m)"
    )
    MAX_GEOJSON_URL_LENGTH: int = Field(
        default=6000, description="Max URL length before falling back to polyline"
    )

    # Verification settings (Gemini Deep Research)
    ENABLE_VERIFICATION: bool = True
    VERIFICATION_CONCURRENCY: int = 4  # Max parallel Gemini requests
    GEMINI_POLL_INTERVAL: int = 60  # Seconds between status checks
    GEMINI_MAX_WAIT_TIME: int = 3600  # Max wait time (60 min) for Gemini response
    VERIFICATION_MAX_RETRIES: int = 2  # Max retries on verification failure
    VERIFICATION_RETRY_DELAY: int = 30  # Seconds to wait between retries

    class Config:
        env_file = _ENV_FILE
        extra = "ignore"  # Ignore extra environment variables


settings = NeighborSettings()


def get_settings() -> NeighborSettings:
    """Get the settings instance."""
    return settings
