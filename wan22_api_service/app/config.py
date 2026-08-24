from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_name: str = "Wan2.2 I2V API"
    app_version: str = "0.1.0"

    data_root: Path = Path("/var/lib/verbascene/wan22-api")
    wan_repo_dir: Path = Path("/opt/verbascene/Wan2.2")
    wan_ckpt_dir: Path = Path("/opt/verbascene/models/Wan2.2-I2V-A14B")
    python_bin: str = "python"
    torchrun_bin: str = "torchrun"

    default_gpu_devices: str = "0,1"
    default_mode: str = Field(default="multi_fsdp", pattern="^(multi_fsdp|single_offload)$")
    default_size: str = "1280*720"
    request_timeout_seconds: int = 60 * 60 * 3

    host: str = "0.0.0.0"
    port: int = 7862


settings = Settings()
