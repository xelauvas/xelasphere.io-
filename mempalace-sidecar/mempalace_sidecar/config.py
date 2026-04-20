from pathlib import Path
from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MEMPALACE_",
        env_file=None,  # env file supplied via systemd EnvironmentFile
        extra="ignore",
        populate_by_name=True,  # Permits bare-name matches for validation_alias
    )

    socket_path: str = Field(default="/run/mempalace/sidecar.sock")
    socket_group: str = Field(default="mempalace")
    token: str = Field(min_length=32)
    data_dir: Path
    cache_max_bytes: int = Field(default=2 * 1024**3)
    idle_ttl_sec: int = Field(default=14400)
    embed_model: str = Field(default="all-MiniLM-L6-v2")
    kg_llm_provider: str = Field(default="ollama")
    kg_daily_usd_cap: float = Field(default=5.0)
    log_level: str = Field(default="info")
    prometheus_multiproc_dir: Path = Field(
        default=Path("/var/run/mempalace/prom"),
        validation_alias=AliasChoices("PROMETHEUS_MULTIPROC_DIR", "MEMPALACE_PROMETHEUS_MULTIPROC_DIR"),
    )
    shadow_sample_rate: float = Field(default=0.20, ge=0.0, le=1.0)
    user_id_hash_salt: str = Field(min_length=8)

    @field_validator("data_dir")
    @classmethod
    def _must_exist(cls, v: Path) -> Path:
        v = Path(v)
        v.mkdir(parents=True, exist_ok=True)
        return v
