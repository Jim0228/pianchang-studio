from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ProjectCreate(BaseModel):
    name: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=8000)
    platform: str = "douyin"
    duration: int = Field(default=15, ge=5, le=90)
    ratio: str = "9:16"

    @model_validator(mode="after")
    def _require_identity(self):
        if not self.name.strip() and not self.description.strip():
            raise ValueError("项目名或商品描述至少填写一项")
        return self


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=8000)
    platform: str | None = None
    duration: int | None = Field(default=None, ge=5, le=90)
    ratio: str | None = None
    status: str | None = None


class ProductUpdate(BaseModel):
    title: str | None = None
    category: str | None = None
    selling_points: list[str] | None = None
    audience: str | None = None
    price: str | None = None
    tone: str | None = None


class RecommendRequest(BaseModel):
    project_id: str


class ScriptCreate(BaseModel):
    skill_id: str
    platform: str = "douyin"
    duration: int = Field(default=15, ge=5, le=90)
    ratio: str = "9:16"


class ShotPatch(BaseModel):
    idx: int
    seconds: float = Field(default=3.0, ge=1.0, le=30.0)
    scene: str = ""
    camera: str = ""
    action: str = ""
    voiceover: str = ""
    on_screen: str = ""
    prompt: str = ""


class ScriptPatch(BaseModel):
    title: str | None = None
    caption_title: str | None = None
    caption_body: str | None = None
    hashtags: list[str] | None = None
    shots: list[ShotPatch] | None = None


class SettingsUpdate(BaseModel):
    ai_mode: Literal["mock", "real"] | None = None
    video_provider: Literal["mock", "seedance", "mcp", "rest"] | None = None
    image_provider: Literal["mock", "ark", "rest"] | None = None
    llm_provider: Literal["mock", "ark", "deepseek"] | None = None


class ExportResponse(BaseModel):
    id: str
    download_url: str
    filename: str
    size: int
