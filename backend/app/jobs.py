from __future__ import annotations

import asyncio
import io
import logging
import shutil
import traceback
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps
from sqlalchemy.orm import Session

from .config import Settings
from .db import SessionLocal
from .models import Asset, Generation, Product, Project, Script, Shot, Skill, Video
from .errors import AppError
from .services.audio import fit_voice, mix_video
from .services.ffmpeg import concat, make_clip, normalize_video, probe
from .services.keyframes import (
    ensure_ai_label,
    ensure_plate,
    generate_cover,
    generate_keyframe,
    generate_subtitle_png,
)
from .services.music import generate_bgm
from .services.storyboard import (
    build_storyboard_review,
    build_video_prompt,
    generate_storyboard,
)
from .services.tts import synthesize, wav_duration
from .utils import ensure_parent, get_font, json_dumps, json_loads, new_id, sha256_file

logger = logging.getLogger(__name__)


def _update_generation(
    generation_id: str,
    *,
    status: str | None = None,
    progress: int | None = None,
    message: str | None = None,
    result: dict | None = None,
    error: str | None = None,
) -> None:
    session = SessionLocal()
    try:
        generation = session.get(Generation, generation_id)
        if generation is None:
            return
        if status is not None:
            generation.status = status
        if progress is not None:
            generation.progress = max(0, min(100, progress))
        if message is not None:
            generation.message = message
        if result is not None:
            generation.result_json = json_dumps(result)
        if error is not None:
            generation.error = error
        session.commit()
    finally:
        session.close()


def _fail(generation_id: str, project_id: str | None, exc: Exception) -> None:
    logger.error("generation failed: %s", exc)
    logger.debug(traceback.format_exc())
    _update_generation(generation_id, status="failed", message="生成失败", error=str(exc)[:1000])
    if project_id:
        session = SessionLocal()
        try:
            project = session.get(Project, project_id)
            if project:
                project.status = "error"
                session.commit()
        finally:
            session.close()


def _previs_one(project_id: str, script_id: str, shot_id: str, total: int, settings: Settings) -> None:
    session = SessionLocal()
    try:
        project = session.get(Project, project_id)
        script = session.get(Script, script_id)
        shot = session.get(Shot, shot_id)
        product = session.query(Product).filter(Product.project_id == project_id).first()
        if not (project and script and shot and product):
            raise RuntimeError("预演数据不完整")
        skill = session.get(Skill, script.skill_id)
        if skill is None:
            raise RuntimeError("视频风格不存在")
        brief = json_loads(product.brief_json, {})
        config = json_loads(skill.config_json, {})
        generate_keyframe(session, settings, project, script, shot, config, brief, total)
    finally:
        session.close()


def _storyboard_stage_enabled(settings: Settings) -> bool:
    """真实模式下，预演产出 REST 分镜图；mock 模式仍用 Pillow 海报，保证零成本演示。"""
    return settings.is_real and settings.image_provider_name == "rest"


async def _storyboard_one(
    project_id: str, script_id: str, shot_id: str, settings: Settings, *, force: bool = False
) -> None:
    session = SessionLocal()
    try:
        project = session.get(Project, project_id)
        script = session.get(Script, script_id)
        shot = session.get(Shot, shot_id)
        if not (project and script and shot):
            raise RuntimeError("分镜图数据不完整")
        skill = session.get(Skill, script.skill_id)
        product = session.query(Product).filter(Product.project_id == project_id).first()
        if skill is None or product is None:
            raise RuntimeError("分镜图缺少风格或商品档案")
        await generate_storyboard(
            session,
            settings,
            project,
            script,
            shot,
            json_loads(skill.config_json, {}),
            json_loads(product.brief_json, {}),
            force=force,
        )
    finally:
        session.close()


async def _ensure_frame_one(
    project_id: str,
    script_id: str,
    shot_id: str,
    total: int,
    settings: Settings,
    *,
    force: bool = False,
) -> None:
    """优先真实分镜图；失败时回退 Pillow 海报，不阻断整条链路。"""
    if _storyboard_stage_enabled(settings):
        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                await _storyboard_one(project_id, script_id, shot_id, settings, force=force)
                return
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning("REST 分镜图第 %s 次失败：%s", attempt + 1, exc)
                _record_shot_error(shot_id, str(exc))
                if attempt == 0:
                    await asyncio.sleep(5)
        logger.error("REST 分镜图重试仍失败，回退 Pillow 海报：%s", last_exc)
    await asyncio.to_thread(_previs_one, project_id, script_id, shot_id, total, settings)


def _record_shot_error(shot_id: str, message: str) -> None:
    session = SessionLocal()
    try:
        shot = session.get(Shot, shot_id)
        if shot is not None:
            shot.error = message[:500]
            session.commit()
    finally:
        session.close()


def _storyboard_review_one(project_id: str, script_id: str, settings: Settings) -> dict:
    session = SessionLocal()
    try:
        project = session.get(Project, project_id)
        script = session.get(Script, script_id)
        product = session.query(Product).filter(Product.project_id == project_id).first()
        if not (project and script and product):
            raise RuntimeError("分镜审核数据不完整")
        skill = session.get(Skill, script.skill_id)
        return build_storyboard_review(
            session,
            settings,
            project,
            script,
            json_loads(skill.config_json, {}) if skill else {},
            json_loads(product.brief_json, {}),
        )
    finally:
        session.close()


async def regenerate_shot_storyboard(generation_id: str, shot_id: str, settings: Settings) -> None:
    """只重生成一张分镜图：保留其它镜头，清掉这一镜的旧视频等待重渲。"""
    project_id: str | None = None
    script_id: str | None = None
    total = 0
    session = SessionLocal()
    try:
        shot = session.get(Shot, shot_id)
        if shot is None:
            raise RuntimeError("镜头不存在")
        script = session.get(Script, shot.script_id)
        if script is None:
            raise RuntimeError("脚本不存在")
        project_id = script.project_id
        script_id = script.id
        total = session.query(Shot).filter(Shot.script_id == script_id).count()
        shot.clip_asset_id = None
        shot.subtitle_asset_id = None
        shot.error = ""
        shot.status = "pending"
        session.commit()
        _update_generation(generation_id, status="running", progress=15, message="正在重新生成分镜图")
    finally:
        session.close()

    try:
        if _storyboard_stage_enabled(settings):
            await _storyboard_one(project_id, script_id, shot_id, settings, force=True)
            _update_generation(generation_id, progress=85, message="正在更新联系表")
            review = await asyncio.to_thread(
                _storyboard_review_one, project_id, script_id, settings
            )
            result = {
                "shot_id": shot_id,
                "contact_sheet": review.get("contact_sheet"),
                "scene_anchor": review.get("scene_anchor"),
            }
        else:
            await asyncio.to_thread(_previs_one, project_id, script_id, shot_id, total, settings)
            result = {"shot_id": shot_id}
        _update_generation(
            generation_id,
            status="succeeded",
            progress=100,
            message="分镜图已更新",
            result=result,
        )
    except Exception as exc:  # noqa: BLE001
        _fail(generation_id, project_id, exc)


def _subtitle_one(project_id: str, shot_id: str, settings: Settings) -> None:
    session = SessionLocal()
    try:
        project = session.get(Project, project_id)
        shot = session.get(Shot, shot_id)
        if project and shot:
            generate_subtitle_png(session, settings, project, shot)
    finally:
        session.close()


def _clip_one(project_id: str, script_id: str, shot_id: str, settings: Settings) -> None:
    session = SessionLocal()
    try:
        project = session.get(Project, project_id)
        script = session.get(Script, script_id)
        shot = session.get(Shot, shot_id)
        if not (project and script and shot):
            raise RuntimeError("镜头数据不完整")
        keyframe = session.get(Asset, shot.keyframe_asset_id) if shot.keyframe_asset_id else None
        subtitle = session.get(Asset, shot.subtitle_asset_id) if shot.subtitle_asset_id else None
        if keyframe is None:
            raise RuntimeError(f"镜头 {shot.idx} 缺少关键帧")
        out_path = settings.data_path / "projects" / project_id / "clips" / f"shot_{shot.idx:02d}.mp4"
        ensure_parent(out_path)
        probe_info = make_clip(
            settings,
            settings.data_path / keyframe.path,
            settings.data_path / subtitle.path if subtitle else None,
            out_path,
            float(shot.seconds),
            width=settings.video_width,
            height=settings.video_height,
            fps=settings.fps,
            zoom="in" if shot.idx % 2 else "out",
        )
        asset = Asset(
            id=new_id("ast"),
            project_id=project_id,
            kind="clip",
            filename=out_path.name,
            path=out_path.relative_to(settings.data_path).as_posix(),
            width=probe_info["width"],
            height=probe_info["height"],
            duration=probe_info["duration"],
            sha256=sha256_file(out_path),
            meta_json=json_dumps({"shot_idx": shot.idx, "mode": "mock"}),
        )
        session.add(asset)
        shot.clip_asset_id = asset.id
        shot.status = "video_ready"
        shot.error = ""
        session.commit()
    finally:
        session.close()


def _plate_one(project_id: str, script_id: str, shot_id: str, settings: Settings) -> Asset:
    session = SessionLocal()
    try:
        project = session.get(Project, project_id)
        script = session.get(Script, script_id)
        shot = session.get(Shot, shot_id)
        skill = session.get(Skill, script.skill_id) if script else None
        if not (project and script and shot and skill):
            raise RuntimeError("场景板数据不完整")
        config = json_loads(skill.config_json, {})
        return ensure_plate(session, settings, project, shot, config)
    finally:
        session.close()


async def _rest_plate_one(project_id: str, script_id: str, shot_id: str, settings: Settings) -> Asset:
    from .services.providers.rest_art import RestArtClient

    session = SessionLocal()
    try:
        project = session.get(Project, project_id)
        script = session.get(Script, script_id)
        shot = session.get(Shot, shot_id)
        skill = session.get(Skill, script.skill_id) if script else None
        if not (project and script and shot and skill):
            raise RuntimeError("REST 场景板数据不完整")
        filename = f"shot_{shot.idx:02d}_rest.jpg"
        existing = (
            session.query(Asset)
            .filter(Asset.project_id == project_id, Asset.kind == "plate", Asset.filename == filename)
            .first()
        )
        if existing is not None and (settings.data_path / existing.path).exists():
            return existing
        config = json_loads(skill.config_json, {})
        prompt_cfg = config.get("prompt") or {}
        originals = (
            session.query(Asset)
            .filter(Asset.project_id == project_id, Asset.kind == "original")
            .order_by(Asset.created_at.asc())
            .limit(1)
            .all()
        )
        reference_paths = [settings.data_path / asset.path for asset in originals]
        prompt = (
            f"{prompt_cfg.get('style', 'photorealistic e-commerce photography')}. "
            f"Scene: {shot.scene}. Camera: {shot.camera}. Lighting: {prompt_cfg.get('lighting', 'soft natural light')}. "
            "Place the exact product from the reference image in this scene. "
            "Keep the product shape, proportions, color, material, label and logo unchanged. "
            "Premium commercial quality, 9:16 portrait, no text, no letters, no watermark, no extra objects."
        )
        params = dict(settings.rest_image_params_dict)
        params.setdefault("aspectRatio", "portrait")
        client = RestArtClient(settings)
        data, job = await client.generate_image(prompt, reference_paths, params=params)
        image = Image.open(io.BytesIO(data)).convert("RGB")
        image = ImageOps.fit(image, (settings.video_width, settings.video_height), method=Image.LANCZOS)
        folder = settings.data_path / "projects" / project_id / "plates"
        path = folder / filename
        ensure_parent(path)
        image.save(path, "JPEG", quality=94)
        asset = Asset(
            id=new_id("ast"),
            project_id=project_id,
            kind="plate",
            filename=path.name,
            path=path.relative_to(settings.data_path).as_posix(),
            width=settings.video_width,
            height=settings.video_height,
            sha256=sha256_file(path),
            meta_json=json_dumps(
                {
                    "shot_idx": shot.idx,
                    "mode": "rest_image",
                    "job_id": job.get("job_id"),
                    "model": job.get("model"),
                    "prompt": prompt,
                }
            ),
        )
        session.add(asset)
        session.commit()
        session.refresh(asset)
        return asset
    finally:
        session.close()


async def _ensure_plate_for(project_id: str, script_id: str, shot_id: str, settings: Settings) -> Asset:
    if settings.image_provider_name == "rest":
        try:
            return await _rest_plate_one(project_id, script_id, shot_id, settings)
        except Exception as exc:  # noqa: BLE001
            logger.warning("REST 场景板失败，回退 Pillow：%s", exc)
    return await asyncio.to_thread(_plate_one, project_id, script_id, shot_id, settings)


async def _mcp_clip_one(project_id: str, script_id: str, shot_id: str, settings: Settings) -> None:
    from .services.providers.mcp import McpClient

    session = SessionLocal()
    try:
        project = session.get(Project, project_id)
        script = session.get(Script, script_id)
        shot = session.get(Shot, shot_id)
        if not (project and script and shot):
            raise RuntimeError("镜头数据不完整")
        existing_clip = session.get(Asset, shot.clip_asset_id) if shot.clip_asset_id else None
        if existing_clip is not None and (settings.data_path / existing_clip.path).exists():
            logger.info("镜头 %s 已有成片，复用 %s", shot.idx, existing_clip.path)
            return
        subtitle = session.get(Asset, shot.subtitle_asset_id) if shot.subtitle_asset_id else None
        if subtitle is None:
            subtitle = generate_subtitle_png(session, settings, project, shot)
        originals = (
            session.query(Asset)
            .filter(Asset.project_id == project_id, Asset.kind == "original")
            .order_by(Asset.created_at.asc())
            .limit(1)
            .all()
        )
        original_paths = [settings.data_path / asset.path for asset in originals]
        subtitle_path = settings.data_path / subtitle.path if subtitle else None
        ratio = script.ratio
        skill = session.get(Skill, script.skill_id)
        config = json_loads(skill.config_json, {}) if skill else {}
        prompt = build_video_prompt(shot, config)
        keyframe = session.get(Asset, shot.keyframe_asset_id) if shot.keyframe_asset_id else None
        first_frame = settings.data_path / keyframe.path if keyframe else None
        if first_frame is not None and not first_frame.exists():
            first_frame = None
        shot_idx = shot.idx
        seconds = float(shot.seconds)
    finally:
        session.close()

    if first_frame is None:
        plate = await _ensure_plate_for(project_id, script_id, shot_id, settings)
        first_frame = settings.data_path / plate.path
    raw_path = settings.data_path / "projects" / project_id / "clips" / f"mcp_{shot_idx:02d}.mp4"
    client = McpClient(settings)
    info = await client.generate_video(
        prompt,
        [first_frame, *original_paths],
        raw_path,
        seconds=seconds,
        ratio=ratio,
    )

    label_path = ensure_ai_label(settings, settings.video_width, settings.video_height)
    final_path = settings.data_path / "projects" / project_id / "clips" / f"shot_{shot_idx:02d}.mp4"
    probe_info = await asyncio.to_thread(
        normalize_video,
        settings,
        raw_path,
        final_path,
        seconds=seconds,
        width=settings.video_width,
        height=settings.video_height,
        fps=settings.fps,
        subtitle=subtitle_path,
        label=label_path,
    )

    session = SessionLocal()
    try:
        shot = session.get(Shot, shot_id)
        asset = Asset(
            id=new_id("ast"),
            project_id=project_id,
            kind="clip",
            filename=final_path.name,
            path=final_path.relative_to(settings.data_path).as_posix(),
            width=probe_info["width"],
            height=probe_info["height"],
            duration=probe_info["duration"],
            sha256=sha256_file(final_path),
            meta_json=json_dumps(
                {
                    "shot_idx": shot_idx,
                    "mode": "mcp",
                    "job_id": info.get("job_id"),
                    "source_url": info.get("url"),
                    "params": info.get("params"),
                }
            ),
        )
        session.add(asset)
        shot.clip_asset_id = asset.id
        shot.status = "video_ready"
        shot.error = ""
        session.commit()
    finally:
        session.close()


async def _rest_clip_one(project_id: str, script_id: str, shot_id: str, settings: Settings) -> None:
    from .services.providers.rest_art import RestArtClient

    session = SessionLocal()
    try:
        project = session.get(Project, project_id)
        script = session.get(Script, script_id)
        shot = session.get(Shot, shot_id)
        if not (project and script and shot):
            raise RuntimeError("镜头数据不完整")
        existing_clip = session.get(Asset, shot.clip_asset_id) if shot.clip_asset_id else None
        if existing_clip is not None and (settings.data_path / existing_clip.path).exists():
            logger.info("镜头 %s 已有成片，复用 %s", shot.idx, existing_clip.path)
            return
        subtitle = session.get(Asset, shot.subtitle_asset_id) if shot.subtitle_asset_id else None
        if subtitle is None:
            subtitle = generate_subtitle_png(session, settings, project, shot)
        originals = (
            session.query(Asset)
            .filter(Asset.project_id == project_id, Asset.kind == "original")
            .order_by(Asset.created_at.asc())
            .limit(1)
            .all()
        )
        original_paths = [settings.data_path / asset.path for asset in originals]
        subtitle_path = settings.data_path / subtitle.path if subtitle else None
        ratio = script.ratio
        skill = session.get(Skill, script.skill_id)
        config = json_loads(skill.config_json, {}) if skill else {}
        prompt = build_video_prompt(shot, config)
        keyframe = session.get(Asset, shot.keyframe_asset_id) if shot.keyframe_asset_id else None
        first_frame = settings.data_path / keyframe.path if keyframe else None
        if first_frame is not None and not first_frame.exists():
            first_frame = None
        shot_idx = shot.idx
        seconds = float(shot.seconds)
    finally:
        session.close()

    if first_frame is None:
        plate = await _ensure_plate_for(project_id, script_id, shot_id, settings)
        first_frame = settings.data_path / plate.path
    raw_path = settings.data_path / "projects" / project_id / "clips" / f"rest_{shot_idx:02d}.mp4"
    client = RestArtClient(settings)
    info = await client.generate_video(
        prompt,
        [first_frame, *original_paths],
        raw_path,
        seconds=seconds,
        ratio=ratio,
    )

    label_path = ensure_ai_label(settings, settings.video_width, settings.video_height)
    final_path = settings.data_path / "projects" / project_id / "clips" / f"shot_{shot_idx:02d}.mp4"
    probe_info = await asyncio.to_thread(
        normalize_video,
        settings,
        raw_path,
        final_path,
        seconds=float(shot.seconds),
        width=settings.video_width,
        height=settings.video_height,
        fps=settings.fps,
        subtitle=subtitle_path,
        label=label_path,
    )

    session = SessionLocal()
    try:
        shot = session.get(Shot, shot_id)
        asset = Asset(
            id=new_id("ast"),
            project_id=project_id,
            kind="clip",
            filename=final_path.name,
            path=final_path.relative_to(settings.data_path).as_posix(),
            width=probe_info["width"],
            height=probe_info["height"],
            duration=probe_info["duration"],
            sha256=sha256_file(final_path),
            meta_json=json_dumps(
                {
                    "shot_idx": shot.idx,
                    "mode": "rest_video",
                    "job_id": info.get("job_id"),
                    "model": info.get("model"),
                    "params": info.get("params"),
                    "source_url": info.get("url"),
                }
            ),
        )
        session.add(asset)
        shot.clip_asset_id = asset.id
        shot.status = "video_ready"
        shot.error = ""
        session.commit()
    finally:
        session.close()


def _cover_one(project_id: str, script_id: str, settings: Settings) -> Asset:
    session = SessionLocal()
    try:
        project = session.get(Project, project_id)
        script = session.get(Script, script_id)
        product = session.query(Product).filter(Product.project_id == project_id).first()
        shots = session.query(Shot).filter(Shot.script_id == script_id).order_by(Shot.idx.asc()).all()
        keyframe = None
        for shot in shots:
            if shot.keyframe_asset_id:
                keyframe = session.get(Asset, shot.keyframe_asset_id)
                break
        brief = json_loads(product.brief_json, {}) if product else {}
        return generate_cover(session, settings, project, script, keyframe, brief)
    finally:
        session.close()


async def _real_keyframe_one(project_id: str, script_id: str, shot_id: str, total: int, settings: Settings) -> None:
    from .services.providers.ark import ArkClient

    session = SessionLocal()
    try:
        project = session.get(Project, project_id)
        script = session.get(Script, script_id)
        shot = session.get(Shot, shot_id)
        if not (project and script and shot):
            raise RuntimeError("关键帧数据不完整")
        originals = (
            session.query(Asset)
            .filter(Asset.project_id == project_id, Asset.kind == "original")
            .order_by(Asset.created_at.asc())
            .limit(3)
            .all()
        )
        refs = [settings.data_path / asset.path for asset in originals]
        client = ArkClient(settings)
        data = await client.generate_image(shot.prompt, refs)
        image = Image.open(io.BytesIO(data)).convert("RGB")
        image = ImageOps.fit(image, (settings.video_width, settings.video_height), method=Image.LANCZOS)
        draw = ImageDraw.Draw(image)
        label_font = get_font(22)
        draw.rounded_rectangle(
            [settings.video_width - 150, settings.video_height - 80, settings.video_width - 30, settings.video_height - 36],
            radius=8,
            fill=(0, 0, 0),
        )
        draw.text((settings.video_width - 140, settings.video_height - 74), "AI 生成", font=label_font, fill=(255, 255, 255))
        out_path = settings.data_path / "projects" / project_id / "keyframes" / f"shot_{shot.idx:02d}.jpg"
        ensure_parent(out_path)
        image.save(out_path, "JPEG", quality=92)
        asset = Asset(
            id=new_id("ast"),
            project_id=project_id,
            kind="keyframe",
            filename=out_path.name,
            path=out_path.relative_to(settings.data_path).as_posix(),
            width=settings.video_width,
            height=settings.video_height,
            sha256=sha256_file(out_path),
            meta_json=json_dumps({"shot_idx": shot.idx, "mode": "ark", "prompt": shot.prompt}),
        )
        session.add(asset)
        shot.keyframe_asset_id = asset.id
        shot.status = "keyframe_ready"
        session.commit()
    finally:
        session.close()


async def _real_clip_one(project_id: str, script_id: str, shot_id: str, settings: Settings) -> None:
    from .services.providers.seedance import SeedanceClient

    session = SessionLocal()
    try:
        script = session.get(Script, script_id)
        shot = session.get(Shot, shot_id)
        if not (script and shot):
            raise RuntimeError("镜头数据不完整")
        keyframe = session.get(Asset, shot.keyframe_asset_id) if shot.keyframe_asset_id else None
        out_path = settings.data_path / "projects" / project_id / "clips" / f"shot_{shot.idx:02d}.mp4"
        ensure_parent(out_path)
        client = SeedanceClient(settings)
        info = await client.generate_video(
            shot.prompt,
            settings.data_path / keyframe.path if keyframe else None,
            out_path,
            seconds=float(shot.seconds),
            ratio=script.ratio,
        )
        probe_info = probe(settings, out_path)
        asset = Asset(
            id=new_id("ast"),
            project_id=project_id,
            kind="clip",
            filename=out_path.name,
            path=out_path.relative_to(settings.data_path).as_posix(),
            width=probe_info["width"],
            height=probe_info["height"],
            duration=probe_info["duration"],
            sha256=sha256_file(out_path),
            meta_json=json_dumps({"shot_idx": shot.idx, "mode": "seedance", **info}),
        )
        session.add(asset)
        shot.clip_asset_id = asset.id
        shot.status = "video_ready"
        session.commit()
    finally:
        session.close()


def _voice_one(project_id: str, shot_id: str, settings: Settings) -> Asset | None:
    """为一镜台词生成配音 wav；文本没变则复用。"""
    if not settings.tts_enabled:
        return None
    session = SessionLocal()
    try:
        project = session.get(Project, project_id)
        shot = session.get(Shot, shot_id)
        if not (project and shot):
            return None
        text = " ".join((shot.voiceover or "").split())
        if not text:
            return None
        folder = settings.data_path / "projects" / project_id / "voice"
        path = folder / f"voice_{shot.idx:02d}.wav"
        existing = (
            session.query(Asset)
            .filter(
                Asset.project_id == project_id,
                Asset.kind == "voice",
                Asset.filename == path.name,
            )
            .first()
        )
        if existing is not None:
            meta = json_loads(existing.meta_json, {})
            if meta.get("text") == text and int(meta.get("rate", settings.tts_rate)) == settings.tts_rate and path.exists():
                return existing

        ensure_parent(path)
        synthesize(text, path, settings=settings)
        duration = wav_duration(path)
        meta_json = json_dumps(
            {
                "shot_idx": shot.idx,
                "text": text,
                "duration": duration,
                "rate": settings.tts_rate,
                "voice": settings.tts_voice,
            }
        )
        if existing is not None:
            asset = existing
            asset.path = path.relative_to(settings.data_path).as_posix()
            asset.duration = duration
            asset.sha256 = sha256_file(path)
            asset.meta_json = meta_json
        else:
            asset = Asset(
                id=new_id("ast"),
                project_id=project_id,
                kind="voice",
                filename=path.name,
                path=path.relative_to(settings.data_path).as_posix(),
                width=0,
                height=0,
                duration=duration,
                sha256=sha256_file(path),
                meta_json=meta_json,
            )
            session.add(asset)
        session.commit()
        session.refresh(asset)
        return asset
    finally:
        session.close()


def _bgm_one(
    project_id: str, script_id: str, settings: Settings, duration: float
) -> Asset | None:
    """整条视频一首 BGM：一次生成、与成片等长，不做逐镜拼接。"""
    if not settings.bgm_enabled or duration <= 0.5:
        return None
    session = SessionLocal()
    try:
        project = session.get(Project, project_id)
        script = session.get(Script, script_id)
        if not (project and script):
            return None
        skill = session.get(Skill, script.skill_id)
        config = json_loads(skill.config_json, {}) if skill else {}
        mood = settings.bgm_mood.strip() or str((config.get("audio") or {}).get("bgm_mood") or "warm")
        folder = settings.data_path / "projects" / project_id / "audio"
        path = folder / f"bgm_{script_id}.wav"
        existing = (
            session.query(Asset)
            .filter(
                Asset.project_id == project_id,
                Asset.kind == "bgm",
                Asset.filename == path.name,
            )
            .first()
        )
        if (
            existing is not None
            and abs(float(existing.duration or 0) - duration) < 0.4
            and path.exists()
            and json_loads(existing.meta_json, {}).get("mood") == mood
        ):
            return existing

        ensure_parent(path)
        generate_bgm(path, duration, mood)
        meta_json = json_dumps(
            {"duration": duration, "mood": mood, "engine": "local-synth", "scope": "whole_video"}
        )
        if existing is not None:
            asset = existing
            asset.path = path.relative_to(settings.data_path).as_posix()
            asset.duration = duration
            asset.sha256 = sha256_file(path)
            asset.meta_json = meta_json
        else:
            asset = Asset(
                id=new_id("ast"),
                project_id=project_id,
                kind="bgm",
                filename=path.name,
                path=path.relative_to(settings.data_path).as_posix(),
                width=0,
                height=0,
                duration=duration,
                sha256=sha256_file(path),
                meta_json=meta_json,
            )
            session.add(asset)
        session.commit()
        session.refresh(asset)
        return asset
    finally:
        session.close()


def _voice_segments(
    shots: list[dict], voice_assets: dict[int, Asset], settings: Settings, duration: float
) -> list[tuple[Path, float]]:
    """每句台词放进自己镜头的时隙：不重叠、不越界，超长时轻微加速。"""
    entries: list[tuple[float, Path, float]] = []
    start = 0.0
    for shot in shots:
        asset = voice_assets.get(int(shot["idx"]))
        if asset is not None:
            source = settings.data_path / asset.path
            if source.exists():
                entries.append((start, source, float(asset.duration or wav_duration(source))))
        start += float(shot["seconds"])
    if not entries:
        return []

    gap = 0.08
    segments: list[tuple[Path, float]] = []
    cursor = 0.0
    for index, (planned_start, source, raw_duration) in enumerate(entries):
        next_start = entries[index + 1][0] if index + 1 < len(entries) else duration
        at = max(planned_start, cursor + gap)
        at = min(at, max(0.0, duration - 0.5))
        slot_end = min(duration - 0.05, max(next_start, at + 0.7))
        slot = max(0.5, slot_end - at)
        fitted = source
        length = raw_duration
        if raw_duration > slot:
            fitted, length = fit_voice(settings, source, slot, cap=2.2)
        segments.append((fitted, round(at, 3)))
        cursor = at + length
    return segments


async def run_previs(
    generation_id: str, script_id: str, settings: Settings, *, force: bool = False
) -> None:
    project_id: str | None = None
    keyframe_ark = settings.image_provider_name == "ark"
    storyboard_stage = _storyboard_stage_enabled(settings)
    session = SessionLocal()
    try:
        script = session.get(Script, script_id)
        if script is None:
            raise RuntimeError("脚本不存在")
        project_id = script.project_id
        project = session.get(Project, project_id)
        shots = session.query(Shot).filter(Shot.script_id == script_id).order_by(Shot.idx.asc()).all()
        shot_ids = [shot.id for shot in shots]
        total = len(shot_ids)
        _update_generation(
            generation_id,
            status="running",
            progress=3,
            message="准备分镜图" if storyboard_stage else "准备关键帧",
        )
        if project:
            project.status = "previs_running"
            session.commit()
    finally:
        session.close()

    try:
        label = "分镜图" if storyboard_stage else "关键帧"
        for index, shot_id in enumerate(shot_ids, start=1):
            if keyframe_ark:
                await _real_keyframe_one(project_id, script_id, shot_id, total, settings)
            else:
                await _ensure_frame_one(project_id, script_id, shot_id, total, settings, force=force)
            progress = 5 + int(85 * index / max(1, total))
            _update_generation(generation_id, progress=progress, message=f"已生成{label} {index}/{total}")

        result: dict = {"script_id": script_id, "keyframes": total}
        if storyboard_stage:
            _update_generation(generation_id, progress=95, message="正在合成分镜联系表")
            review = await asyncio.to_thread(_storyboard_review_one, project_id, script_id, settings)
            result = {
                "script_id": script_id,
                "storyboards": total,
                "contact_sheet": review.get("contact_sheet"),
            }
        _update_generation(
            generation_id,
            status="succeeded",
            progress=100,
            message=f"{label}已生成",
            result=result,
        )
        session = SessionLocal()
        try:
            project = session.get(Project, project_id)
            if project:
                project.status = "previs_ready"
                session.commit()
        finally:
            session.close()
    except Exception as exc:  # noqa: BLE001
        _fail(generation_id, project_id, exc)


async def _run_clip(
    provider: str, project_id: str, script_id: str, shot_id: str, settings: Settings
) -> None:
    """单镜生成，失败重试一次（网关排队与瞬时错误很常见）。"""
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            if provider == "rest":
                await _rest_clip_one(project_id, script_id, shot_id, settings)
            elif provider == "mcp":
                await _mcp_clip_one(project_id, script_id, shot_id, settings)
            elif provider == "seedance":
                await _real_clip_one(project_id, script_id, shot_id, settings)
            else:
                await asyncio.to_thread(_clip_one, project_id, script_id, shot_id, settings)
            return
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            logger.warning("镜头 %s 第 %s 次生成失败：%s", shot_id, attempt + 1, exc)
            _record_shot_error(shot_id, str(exc))
            if attempt == 0:
                await asyncio.sleep(8)
    if last_exc is not None:
        raise last_exc


async def run_render(generation_id: str, script_id: str, settings: Settings) -> None:
    project_id: str | None = None
    keyframe_ark = settings.image_provider_name == "ark"
    provider = settings.video_provider_name
    storyboard_stage = _storyboard_stage_enabled(settings)
    session = SessionLocal()
    try:
        script = session.get(Script, script_id)
        if script is None:
            raise RuntimeError("脚本不存在")
        project_id = script.project_id
        shots = session.query(Shot).filter(Shot.script_id == script_id).order_by(Shot.idx.asc()).all()
        shot_ids = [shot.id for shot in shots]
        total = len(shot_ids)
        _update_generation(generation_id, status="running", progress=2, message="准备生成成片")
        project = session.get(Project, project_id)
        if project:
            project.status = "rendering"
            session.commit()
    finally:
        session.close()

    try:
        for index, shot_id in enumerate(shot_ids, start=1):
            if keyframe_ark:
                await _real_keyframe_one(project_id, script_id, shot_id, total, settings)
            else:
                await _ensure_frame_one(project_id, script_id, shot_id, total, settings)
            _update_generation(
                generation_id,
                progress=5 + int(20 * index / max(1, total)),
                message=f"校准{'分镜图' if storyboard_stage else '关键帧'} {index}/{total}",
            )

        for shot_id in shot_ids:
            await asyncio.to_thread(_subtitle_one, project_id, shot_id, settings)

        # REST 视频单镜排队 2-15 分钟，串行会拖到半小时以上；限流并发 2 路。
        if provider == "rest" and total > 1:
            semaphore = asyncio.Semaphore(2)
            done = 0
            counter_lock = asyncio.Lock()

            async def worker(sid: str) -> None:
                nonlocal done
                async with semaphore:
                    await _run_clip(provider, project_id, script_id, sid, settings)
                async with counter_lock:
                    done += 1
                    _update_generation(
                        generation_id,
                        progress=25 + int(60 * done / max(1, total)),
                        message=f"已生成镜头 {done}/{total}",
                    )

            results = await asyncio.gather(*(worker(sid) for sid in shot_ids), return_exceptions=True)
            failures = [item for item in results if isinstance(item, BaseException)]
            if failures:
                raise failures[0]
        else:
            for index, shot_id in enumerate(shot_ids, start=1):
                await _run_clip(provider, project_id, script_id, shot_id, settings)
                _update_generation(
                    generation_id,
                    progress=25 + int(60 * index / max(1, total)),
                    message=f"已生成镜头 {index}/{total}",
                )

        session = SessionLocal()
        try:
            shots = session.query(Shot).filter(Shot.script_id == script_id).order_by(Shot.idx.asc()).all()
            clip_paths = []
            for shot in shots:
                if shot.clip_asset_id:
                    asset = session.get(Asset, shot.clip_asset_id)
                    if asset:
                        clip_paths.append(settings.data_path / asset.path)
            if not clip_paths:
                raise RuntimeError("没有可拼接的镜头")
            video_dir = settings.data_path / "projects" / project_id / "videos"
            silent_path = video_dir / f"{script_id}_silent.mp4"
            final_path = video_dir / f"{script_id}.mp4"
            ensure_parent(final_path)
            shot_plan = [
                {"idx": shot.idx, "seconds": float(shot.seconds), "voiceover": shot.voiceover or ""}
                for shot in shots
            ]
            shot_id_by_idx = {shot.idx: shot.id for shot in shots}
        finally:
            session.close()

        _update_generation(generation_id, progress=86, message="正在拼接画面")
        probe_info = await asyncio.to_thread(concat, settings, clip_paths, silent_path)
        duration = float(probe_info.get("duration") or sum(item["seconds"] for item in shot_plan))

        audio_info: dict = {}
        audio_error = ""
        try:
            _update_generation(generation_id, progress=90, message="正在生成 AI 配音")
            voice_assets: dict[int, Asset] = {}
            for item in shot_plan:
                if item["voiceover"].strip():
                    asset = await asyncio.to_thread(
                        _voice_one, project_id, shot_id_by_idx[item["idx"]], settings
                    )
                    if asset is not None:
                        voice_assets[item["idx"]] = asset
            _update_generation(generation_id, progress=93, message="正在生成整条背景音乐")
            bgm = await asyncio.to_thread(_bgm_one, project_id, script_id, settings, duration)
            segments = await asyncio.to_thread(
                _voice_segments, shot_plan, voice_assets, settings, duration
            )
            if bgm is not None or segments:
                _update_generation(generation_id, progress=96, message="正在混音")
                audio_info = await asyncio.to_thread(
                    mix_video,
                    settings,
                    silent_path,
                    final_path,
                    bgm=(settings.data_path / bgm.path) if bgm is not None else None,
                    voices=segments,
                    duration=duration,
                    bgm_volume=settings.bgm_volume,
                    voice_volume=settings.tts_volume,
                )
                audio_info["engine"] = "sapi+local-synth"
            else:
                shutil.copy2(silent_path, final_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("配音/混音失败，回退为无旁白成片：%s", exc)
            audio_error = str(exc)[:300]
            shutil.copy2(silent_path, final_path)

        _update_generation(generation_id, progress=97, message="正在生成封面")
        cover = await asyncio.to_thread(_cover_one, project_id, script_id, settings)

        session = SessionLocal()
        try:
            session.query(Video).filter(Video.script_id == script_id).delete()
            video = Video(
                id=new_id("vid"),
                project_id=project_id,
                script_id=script_id,
                path=final_path.relative_to(settings.data_path).as_posix(),
                cover_asset_id=cover.id,
                duration=probe_info["duration"],
                width=probe_info["width"],
                height=probe_info["height"],
                status="ready",
                meta_json=json_dumps(
                    {
                        "mode": settings.ai_mode,
                        "video_provider": provider,
                        "codec": probe_info,
                        "audio": audio_info,
                        "audio_error": audio_error,
                    }
                ),
            )
            session.add(video)
            project = session.get(Project, project_id)
            if project:
                project.status = "ready"
            session.commit()
            video_id = video.id
        finally:
            session.close()

        _update_generation(
            generation_id,
            status="succeeded",
            progress=100,
            message="成片已生成",
            result={"video_id": video_id, "script_id": script_id},
        )
    except Exception as exc:  # noqa: BLE001
        _fail(generation_id, project_id, exc)


async def regenerate_shot(shot_id: str, settings: Settings, generation_id: str | None = None) -> None:
    session = SessionLocal()
    try:
        shot = session.get(Shot, shot_id)
        if shot is None:
            return
        shot.keyframe_asset_id = None
        shot.subtitle_asset_id = None
        shot.clip_asset_id = None
        shot.status = "pending"
        script = session.get(Script, shot.script_id)
        project_id = script.project_id if script else None
        session.commit()
    finally:
        session.close()
    if project_id:
        if generation_id is None:
            generation_id = new_id("gen")
            session = SessionLocal()
            try:
                session.add(Generation(id=generation_id, project_id=project_id, stage="previs", status="queued"))
                session.commit()
            finally:
                session.close()
        await run_previs(generation_id, shot.script_id, settings)
