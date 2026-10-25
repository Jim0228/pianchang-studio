from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Project(Base):
    __tablename__ = "projects"

    id = Column(String(64), primary_key=True)
    name = Column(String(200), nullable=False, default="")
    description = Column(Text, nullable=False, default="")
    platform = Column(String(32), nullable=False, default="douyin")
    duration = Column(Integer, nullable=False, default=15)
    ratio = Column(String(16), nullable=False, default="9:16")
    status = Column(String(32), nullable=False, default="draft")
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class Product(Base):
    __tablename__ = "products"

    id = Column(String(64), primary_key=True)
    project_id = Column(String(64), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    brief_json = Column(Text, nullable=False, default="{}")
    title = Column(String(200), nullable=False, default="")
    category = Column(String(64), nullable=False, default="")
    selling_points_json = Column(Text, nullable=False, default="[]")
    audience = Column(String(200), nullable=False, default="")
    price = Column(String(32), nullable=False, default="")
    tone = Column(String(200), nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class Skill(Base):
    __tablename__ = "skills"

    id = Column(String(64), primary_key=True)
    name = Column(String(100), nullable=False)
    category = Column(String(32), nullable=False, default="conversion")
    goal = Column(String(32), nullable=False, default="转化")
    version = Column(Integer, nullable=False, default=1)
    config_json = Column(Text, nullable=False, default="{}")
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)


class Asset(Base):
    __tablename__ = "assets"

    id = Column(String(64), primary_key=True)
    project_id = Column(String(64), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    kind = Column(String(32), nullable=False, default="original")
    filename = Column(String(255), nullable=False, default="")
    path = Column(String(500), nullable=False, default="")
    width = Column(Integer, nullable=False, default=0)
    height = Column(Integer, nullable=False, default=0)
    duration = Column(Float, nullable=False, default=0.0)
    sha256 = Column(String(64), nullable=False, default="")
    meta_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, nullable=False, default=utcnow)


class Script(Base):
    __tablename__ = "scripts"

    id = Column(String(64), primary_key=True)
    project_id = Column(String(64), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    skill_id = Column(String(64), nullable=False, default="")
    platform = Column(String(32), nullable=False, default="douyin")
    ratio = Column(String(16), nullable=False, default="9:16")
    duration = Column(Integer, nullable=False, default=15)
    script_json = Column(Text, nullable=False, default="{}")
    status = Column(String(32), nullable=False, default="draft")
    version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class Shot(Base):
    __tablename__ = "shots"

    id = Column(String(64), primary_key=True)
    script_id = Column(String(64), ForeignKey("scripts.id", ondelete="CASCADE"), nullable=False, index=True)
    idx = Column(Integer, nullable=False, default=0)
    seconds = Column(Float, nullable=False, default=3.0)
    beat = Column(String(32), nullable=False, default="")
    scene = Column(Text, nullable=False, default="")
    camera = Column(String(100), nullable=False, default="")
    action = Column(Text, nullable=False, default="")
    voiceover = Column(Text, nullable=False, default="")
    on_screen = Column(Text, nullable=False, default="")
    prompt = Column(Text, nullable=False, default="")
    keyframe_asset_id = Column(String(64), nullable=True)
    subtitle_asset_id = Column(String(64), nullable=True)
    clip_asset_id = Column(String(64), nullable=True)
    status = Column(String(32), nullable=False, default="pending")
    error = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (Index("ix_shots_script_idx", "script_id", "idx"),)


class Generation(Base):
    __tablename__ = "generations"

    id = Column(String(64), primary_key=True)
    project_id = Column(String(64), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    stage = Column(String(32), nullable=False, default="previs")
    status = Column(String(32), nullable=False, default="queued")
    progress = Column(Integer, nullable=False, default=0)
    message = Column(Text, nullable=False, default="")
    result_json = Column(Text, nullable=False, default="{}")
    error = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class Video(Base):
    __tablename__ = "videos"

    id = Column(String(64), primary_key=True)
    project_id = Column(String(64), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    script_id = Column(String(64), ForeignKey("scripts.id", ondelete="CASCADE"), nullable=False, index=True)
    path = Column(String(500), nullable=False, default="")
    cover_asset_id = Column(String(64), nullable=True)
    duration = Column(Float, nullable=False, default=0.0)
    width = Column(Integer, nullable=False, default=0)
    height = Column(Integer, nullable=False, default=0)
    status = Column(String(32), nullable=False, default="ready")
    meta_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, nullable=False, default=utcnow)


class Export(Base):
    __tablename__ = "exports"

    id = Column(String(64), primary_key=True)
    project_id = Column(String(64), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    video_id = Column(String(64), ForeignKey("videos.id", ondelete="CASCADE"), nullable=False, index=True)
    path = Column(String(500), nullable=False, default="")
    manifest_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, nullable=False, default=utcnow)
