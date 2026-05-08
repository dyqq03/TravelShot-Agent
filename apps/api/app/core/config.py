from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return ["http://localhost:3000", "http://127.0.0.1:3000"]
    return [item.strip() for item in value.split(",") if item.strip()]


def _clean_secret(value: str | None) -> str | None:
    if not value:
        return None
    stripped = value.strip()
    if not stripped or stripped == "your_key_here":
        return None
    return stripped


class Settings(BaseSettings):
    app_name: str = "TravelShot Agent API"
    app_env: str = Field(default="development", alias="APP_ENV")
    database_url: str = Field(
        default="postgresql+asyncpg://travelshot:travelshot_dev_password@localhost:5433/travelshot",
        alias="DATABASE_URL",
    )
    redis_url: str = Field(default="redis://localhost:6380/0", alias="REDIS_URL")
    cors_origins_raw: str = Field(
        default="http://localhost:3000,http://127.0.0.1:3000",
        alias="CORS_ORIGINS",
    )
    api_access_token_raw: str | None = Field(default=None, alias="API_ACCESS_TOKEN")
    api_rate_limit_per_minute: int = Field(default=60, alias="API_RATE_LIMIT_PER_MINUTE")
    require_runtime_services: bool = Field(default=True, alias="REQUIRE_RUNTIME_SERVICES")
    runtime_check_timeout_seconds: float = Field(default=0.75, alias="RUNTIME_CHECK_TIMEOUT_SECONDS")
    spot_data_dir: str = Field(default="db/seed/spots", alias="SPOT_DATA_DIR")
    import_seed_spots_on_startup: bool = Field(default=True, alias="IMPORT_SEED_SPOTS_ON_STARTUP")
    open_meteo_base_url: str = Field(default="https://api.open-meteo.com", alias="OPEN_METEO_BASE_URL")
    weather_timeout_seconds: float = Field(default=5.0, alias="WEATHER_TIMEOUT_SECONDS")
    tool_cache_ttl_seconds: int = Field(default=1800, alias="TOOL_CACHE_TTL_SECONDS")
    plan_cache_ttl_seconds: int = Field(default=86400, alias="PLAN_CACHE_TTL_SECONDS")
    history_retention_days: int = Field(default=7, alias="HISTORY_RETENTION_DAYS")
    nominatim_base_url: str = Field(default="https://nominatim.openstreetmap.org", alias="NOMINATIM_BASE_URL")
    nominatim_email: str | None = Field(default=None, alias="NOMINATIM_EMAIL")
    nominatim_timeout_seconds: float = Field(default=8.0, alias="NOMINATIM_TIMEOUT_SECONDS")
    amap_api_key_raw: str | None = Field(default=None, alias="AMAP_API_KEY")
    maps_api_key: str | None = Field(default=None, alias="MAPS_API_KEY")
    amap_base_url: str = Field(default="https://restapi.amap.com", alias="AMAP_BASE_URL")
    amap_timeout_seconds: float = Field(default=5.0, alias="AMAP_TIMEOUT_SECONDS")
    tavily_api_key_raw: str | None = Field(default=None, alias="TAVILY_API_KEY")
    search_api_key: str | None = Field(default=None, alias="SEARCH_API_KEY")
    tavily_base_url: str = Field(default="https://api.tavily.com", alias="TAVILY_BASE_URL")
    tavily_timeout_seconds: float = Field(default=8.0, alias="TAVILY_TIMEOUT_SECONDS")
    tavily_search_depth: str = Field(default="basic", alias="TAVILY_SEARCH_DEPTH")
    tavily_max_results: int = Field(default=5, alias="TAVILY_MAX_RESULTS")
    llm_api_key: str | None = Field(default=None, alias="LLM_API_KEY")
    llm_base_url: str = Field(default="https://dashscope.aliyuncs.com/compatible-mode/v1", alias="LLM_BASE_URL")
    llm_model: str = Field(default="qwen-plus", alias="LLM_MODEL")
    llm_goal_parser_mode: str = Field(default="always", alias="LLM_GOAL_PARSER_MODE")
    llm_plan_repair_mode: str = Field(default="auto", alias="LLM_PLAN_REPAIR_MODE")
    llm_timeout_seconds: float = Field(default=90.0, alias="LLM_TIMEOUT_SECONDS")
    llm_max_retries: int = Field(default=2, alias="LLM_MAX_RETRIES")
    llm_retry_base_delay_seconds: float = Field(default=1.5, alias="LLM_RETRY_BASE_DELAY_SECONDS")
    llm_max_tokens: int = Field(default=6144, alias="LLM_MAX_TOKENS")
    agent_max_llm_calls: int = Field(default=7, alias="AGENT_MAX_LLM_CALLS")
    agent_max_tool_rounds: int = Field(default=4, alias="AGENT_MAX_TOOL_ROUNDS")
    agent_max_tool_requests_per_batch: int = Field(default=10, alias="AGENT_MAX_TOOL_REQUESTS_PER_BATCH")
    agent_max_route_requests: int = Field(default=4, alias="AGENT_MAX_ROUTE_REQUESTS")
    agent_route_tools_max_dates: int = Field(default=3, alias="AGENT_ROUTE_TOOLS_MAX_DATES")
    vision_api_key_raw: str | None = Field(default=None, alias="VISION_API_KEY")
    vision_base_url: str = Field(default="https://dashscope.aliyuncs.com/compatible-mode/v1", alias="VISION_BASE_URL")
    vision_model: str = Field(default="qwen-vl-plus", alias="VISION_MODEL")
    vision_timeout_seconds: float = Field(default=60.0, alias="VISION_TIMEOUT_SECONDS")
    vision_max_tokens: int = Field(default=4096, alias="VISION_MAX_TOKENS")

    @property
    def cors_origins(self) -> list[str]:
        return _split_csv(self.cors_origins_raw)

    @property
    def api_access_token(self) -> str | None:
        return _clean_secret(self.api_access_token_raw)

    @property
    def amap_api_key(self) -> str | None:
        return _clean_secret(self.amap_api_key_raw) or _clean_secret(self.maps_api_key)

    @property
    def tavily_api_key(self) -> str | None:
        return _clean_secret(self.tavily_api_key_raw) or _clean_secret(self.search_api_key)

    @property
    def vision_api_key(self) -> str | None:
        return _clean_secret(self.vision_api_key_raw) or _clean_secret(self.llm_api_key)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
