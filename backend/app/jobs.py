from __future__ import annotations

import asyncio
import io
import logging
import traceback
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps
from sqlalchemy.orm import Session

from .config import Settings
from .db import SessionLocal
from .models import Asset, Generation, Product, Project, Script, Shot, Skill, Video
from .errors import AppError
from .services.ffmpeg import concat, make_clip, normalize_video, probe
from .services.keyframes import (
    ensure_ai_label,
    ensure_plate,
    generate_cover,
    generate_keyframe,
    generate_subtitle_png,
)
from .services.storyboard import (
    build_storyboard_review,
    build_video_prompt,
    generate_storyboard,
)
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
            final_path = settings.data_path / "projects" / project_id / "videos" / f"{script_id}.mp4"
            ensure_parent(final_path)
        finally:
            session.close()

        _update_generation(generation_id, progress=88, message="正在拼接成片")
        probe_info = await asyncio.to_thread(concat, settings, clip_paths, final_path)

        _update_generation(generation_id, progress=94, message="正在生成封面")
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
                meta_json=json_dumps({"mode": settings.ai_mode, "video_provider": provider, "codec": probe_info}),
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
