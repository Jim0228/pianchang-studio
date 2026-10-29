from __future__ import annotations

import json
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "AI 电商短视频工作台"
    version: str = "0.4.0"

    ai_mode: str = "mock"  # mock | real

    ark_api_key: str = ""
    ark_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    ark_chat_model: str = "doubao-seed-1-6-250615"
    ark_vision_model: str = "doubao-seed-1-6-vision-250815"
    ark_image_model: str = "doubao-seedream-4-0-250828"
    ark_video_model: str = "doubao-seedance-1-0-pro-250528"
    ark_image_size: str = "1080x1920"

    data_dir: str = "./data"
    database_url: str = ""

    video_width: int = 1080
    video_height: int = 1920
    fps: int = 30

    sync_jobs: bool = False
    ffmpeg_bin: str = ""
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://127.0.0.1:8000"

    # 视频生成提供方：mock | seedance | mcp | rest
    video_provider: str = "mock"
    # 图片生成提供方：mock | ark | rest
    image_provider: str = "mock"
    # LLM 提供方：mock | ark | deepseek
    llm_provider: str = "mock"

    # MCP（保留为回退）
    mcp_video_url: str = ""
    mcp_video_token: str = ""
    mcp_video_tool: str = "video_generate"
    mcp_video_resolution: str = "480p"
    mcp_video_duration: int = 4
    mcp_video_timeout: int = 1200
    mcp_video_poll_seconds: int = 60

    # REST 美术资源（图片 / 视频）
    rest_art_base_url: str = ""
    rest_art_token: str = ""
    rest_video_resolution: str = "480p"
    rest_video_duration: int = 4
    rest_image_params: str = '{"aspectRatio":"portrait","quality":"high"}'
    rest_image_wait_seconds: int = 30
    rest_video_wait_seconds: int = 30
    rest_art_timeout: int = 1200

    # DeepSeek LLM
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"

    # 配音（Windows SAPI，逐镜台词）
    tts_enabled: bool = True
    tts_voice: str = "Microsoft Huihui Desktop"
    tts_rate: int = 1
    tts_volume: float = 1.0

    # 背景音乐（本地合成，整条视频一首）
    bgm_enabled: bool = True
    bgm_volume: float = 0.22
    bgm_mood: str = ""

    @property
    def mcp_configured(self) -> bool:
        return bool(self.mcp_video_url.strip() and self.mcp_video_token.strip())

    @property
    def rest_art_configured(self) -> bool:
        return bool(self.rest_art_base_url.strip() and self.rest_art_token.strip())

    @property
    def deepseek_configured(self) -> bool:
        return bool(self.deepseek_api_key.strip())

    @property
    def rest_image_params_dict(self) -> dict:
        try:
            data = json.loads(self.rest_image_params or "{}")
        except (TypeError, ValueError):
            data = {}
        return data if isinstance(data, dict) else {}

    @property
    def video_provider_name(self) -> str:
        return (self.video_provider or "mock").strip().lower()

    @property
    def image_provider_name(self) -> str:
        return (self.image_provider or "mock").strip().lower()

    @property
    def llm_provider_name(self) -> str:
        return (self.llm_provider or "mock").strip().lower()

    @property
    def data_path(self) -> Path:
        path = Path(self.data_dir)
        if not path.is_absolute():
            path = (BASE_DIR / path).resolve()
        return path

    @property
    def sqlalchemy_url(self) -> str:
        if self.database_url.strip():
            return self.database_url.strip()
        return f"sqlite:///{(self.data_path / 'app.db').as_posix()}"

    @property
    def ark_key_present(self) -> bool:
        return bool(self.ark_api_key.strip())

    @property
    def is_real(self) -> bool:
        return self.ai_mode.lower() == "real"


settings = Settings()
