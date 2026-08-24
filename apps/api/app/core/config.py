from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "VerbaScene API"
    app_version: str = "0.1.0"
    app_env: str = "local"
    debug: bool = True
    log_level: str = "INFO"
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
    persistence_mode: Literal["local", "database"] = "local"
    database_url: str | None = None
    local_data_dir: str = "../../storage/local"
    local_database_filename: str = "content.sqlite3"
    redis_url: str = "redis://localhost:6379/0"
    redis_required: bool = False
    storage_root: str = "../../storage"
    public_storage_base_url: str = "http://localhost:8000/storage"
    ffmpeg_path: str = "ffmpeg"
    provider_timeout_sec: int = 300
    llm_provider: str = "mock"
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_model: str = "mock-llm"
    dashscope_api_key: str | None = None
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    dashscope_model: str = "qwen-plus"
    dashscope_image_api_key: str | None = None
    dashscope_image_base_url: str = "https://dashscope.aliyuncs.com"
    dashscope_image_model: str = "wan2.6-t2i"
    dashscope_image_poll_interval_sec: int = 10
    dashscope_image_job_timeout_sec: int = 1200
    dashscope_image_prompt_extend: bool = True
    dashscope_image_watermark: bool = False
    image_provider: str = "mock"
    image_api_key: str | None = None
    image_base_url: str | None = None
    image_model: str = "mock-image"
    image_size: str = "1280x720"
    image_response_format: str = "b64_json"
    image_reference_transport: str = "hybrid"
    image_supports_references: bool = False
    # Backward-compatible setting only. The production workflow generates one
    # image candidate per target and uses an explicit regenerate click to
    # create the next version.
    image_candidate_count: int = 1
    image_quality: Literal["low", "medium", "high", "auto"] = "medium"
    image_num_inference_steps: int | None = None
    image_guidance_scale: float = 4.0
    image_seed: int | None = None
    custom_image_endpoint: str = "/generate"
    custom_image_output_dir: str = "/tmp/verbascene-provider-output"
    custom_image_public_base_url: str | None = None
    qwen_image_musubi_base_url: str | None = None
    qwen_image_musubi_model: str = "qwen-image-fig-lightning8-v2"
    # Backward-compatible environment alias. New runtime configuration writes
    # IMAGE_SUPPORTS_REFERENCES so the capability is not tied to one adapter.
    custom_image_supports_references: bool = False
    comfyui_flux_base_urls: str | None = None
    comfyui_flux_model: str = "flux1-dev.safetensors"
    comfyui_flux_clip_l: str = "clip_l.safetensors"
    comfyui_flux_t5xxl: str = "t5xxl_fp16.safetensors"
    comfyui_flux_vae: str = "ae.safetensors"
    comfyui_flux_weight_dtype: str = "default"
    comfyui_flux_clip_device: str = "default"
    comfyui_flux_sampler: str = "euler"
    comfyui_flux_scheduler: str = "simple"
    comfyui_flux_poll_interval_sec: int = 2
    comfyui_flux_job_timeout_sec: int = 2400
    provider_secret_file: str = ".runtime/provider-secrets.json"
    gemini_api_key: str | None = None
    gemini_image_model: str = "gemini-2.5-flash-image"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    video_provider: str = "mock"
    video_api_key: str | None = None
    video_model: str = "mock-video"
    video_generation_concurrency: int = 2
    seedance2_api_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    seedance2_api_key: str | None = None
    seedance2_model: str = "doubao-seedance-2-0-260128"
    seedance2_api_timeout_sec: int = 60
    seedance2_api_poll_interval_sec: int = 10
    seedance2_api_job_timeout_sec: int = 3600
    seedance2_resolution: str = "480p"
    seedance2_ratio: str = "16:9"
    seedance2_generate_audio: bool = True
    seedance2_watermark: bool = False
    ltx23_api_base_url: str = "http://127.0.0.1:18109"
    ltx23_api_timeout_sec: int = 30
    ltx23_api_poll_interval_sec: int = 5
    ltx23_api_job_timeout_sec: int = 3600
    ltx23_size: str = "1024*576"
    ltx23_seed: int | None = None
    ltx23_frames: int = 81
    ltx23_steps: int = 8
    ltx23_cfg: float = 1.0
    ltx23_fps: float = 16.0
    ltx23_strength: float = 0.78
    wan_i2v_api_base_url: str = "http://127.0.0.1:18083"
    wan_i2v_api_protocol: str = "official"
    wan_i2v_api_key: str | None = None
    wan_i2v_api_mode: str = "multi_fsdp"
    wan_i2v_api_gpu_devices: str = "0,1"
    wan_i2v_api_timeout_sec: int = 30
    wan_i2v_api_poll_interval_sec: int = 10
    wan_i2v_api_job_timeout_sec: int = 3600
    wan_i2v_api_result_variant: str = "native"
    wan_i2v_image_transport: str = "auto"
    wan_i2v_remote_storage_base_url: str | None = None
    wan_i2v_size: str = "1280*720"
    wan_i2v_seed: int | None = None
    wan_i2v_frames: int = 81
    wan_i2v_steps: int = 40
    wan_i2v_guidance: float = 3.5
    wan_i2v_fps: int = 16
    wan_i2v_max_area: int = 921600
    wan_i2v_upscale_1080p: bool = True

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @model_validator(mode="after")
    def migrate_legacy_image_reference_capability(self):
        if (
            "image_supports_references" not in self.model_fields_set
            and self.custom_image_supports_references
        ):
            self.image_supports_references = True
        return self

    @property
    def local_database_url(self) -> str:
        database_path = (
            Path(self.local_data_dir).expanduser().resolve() / self.local_database_filename
        )
        return f"sqlite+pysqlite:///{database_path.as_posix()}"

    @property
    def effective_database_url(self) -> str:
        if self.persistence_mode == "local":
            return self.local_database_url

        configured_url = (self.database_url or "").strip()
        if not configured_url:
            raise ValueError("DATABASE_URL is required when PERSISTENCE_MODE=database")
        return configured_url

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
