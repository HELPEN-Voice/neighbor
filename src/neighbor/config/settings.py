# src/ii_agent/tools/neighbor/config/settings.py
try:
    from pydantic_settings import BaseSettings
except ImportError:
    # Fallback for Pydantic v1
    from pydantic import BaseSettings

from pydantic import Field
from typing import Literal


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
    BATCH_SIZE: int = 5
    MAX_NEIGHBORS: int = 30
    DEFAULT_RADIUS_MILES: float = 0.5
    CONCURRENCY_LIMIT: int = 15  # guardrail if you want to cap in dense areas

    # Future toggles (not used yet; here for forward-compat)
    STREAMING_ENABLED: bool = False
    TRACE_ENABLED: bool = False

    class Config:
        env_file = ".env"
        extra = "ignore"  # Ignore extra environment variables


settings = NeighborSettings()
