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
from .services.ffmpeg import concat, make_clip, normalize_video, probe
from .services.keyframes import (
    ensure_ai_label,
    ensure_plate,
    generate_cover,
    generate_keyframe,
    generate_subtitle_png,
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
    finally:
        session.close()

    plate = await _ensure_plate_for(project_id, script_id, shot_id, settings)
    plate_path = settings.data_path / plate.path

    prompt = (
        "Use the first provided image as the exact first frame. "
        f"{shot.prompt} "
        "Keep the product's shape, proportions, color, material, label, logo and composition unchanged. "
        "Only add subtle camera movement and natural light motion. "
        "Photorealistic e-commerce commercial quality, no text, no letters, no watermark, no extra objects."
    )
    raw_path = settings.data_path / "projects" / project_id / "clips" / f"mcp_{shot.idx:02d}.mp4"
    client = McpClient(settings)
    info = await client.generate_video(
        prompt,
        [plate_path, *original_paths],
        raw_path,
        seconds=float(shot.seconds),
        ratio=ratio,
    )

    label_path = ensure_ai_label(settings, settings.video_width, settings.video_height)
    final_path = settings.data_path / "projects" / project_id / "clips" / f"shot_{shot.idx:02d}.mp4"
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
    finally:
        session.close()

    plate = await _ensure_plate_for(project_id, script_id, shot_id, settings)
    plate_path = settings.data_path / plate.path
    prompt = (
        "Use the first provided image as the exact first frame. "
        f"{shot.prompt} "
        "Keep the product's shape, proportions, color, material, label, logo and composition unchanged. "
        "Only add subtle camera movement and natural light motion. "
        "Photorealistic e-commerce commercial quality, no text, no letters, no watermark, no extra objects."
    )
    raw_path = settings.data_path / "projects" / project_id / "clips" / f"rest_{shot.idx:02d}.mp4"
    client = RestArtClient(settings)
    info = await client.generate_video(
        prompt,
        [plate_path, *original_paths],
        raw_path,
        seconds=float(shot.seconds),
        ratio=ratio,
    )

    label_path = ensure_ai_label(settings, settings.video_width, settings.video_height)
    final_path = settings.data_path / "projects" / project_id / "clips" / f"shot_{shot.idx:02d}.mp4"
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


async def run_previs(generation_id: str, script_id: str, settings: Settings) -> None:
    project_id: str | None = None
    keyframe_ark = settings.image_provider_name == "ark"
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
        _update_generation(generation_id, status="running", progress=3, message="准备关键帧")
        if project:
            project.status = "previs_running"
            session.commit()
    finally:
        session.close()

    try:
        for index, shot_id in enumerate(shot_ids, start=1):
            if keyframe_ark:
                await _real_keyframe_one(project_id, script_id, shot_id, total, settings)
            else:
                await asyncio.to_thread(_previs_one, project_id, script_id, shot_id, total, settings)
            progress = 5 + int(85 * index / max(1, total))
            _update_generation(generation_id, progress=progress, message=f"已生成关键帧 {index}/{total}")
        _update_generation(
            generation_id,
            status="succeeded",
            progress=100,
            message="关键帧已生成",
            result={"script_id": script_id, "keyframes": total},
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


async def run_render(generation_id: str, script_id: str, settings: Settings) -> None:
    project_id: str | None = None
    keyframe_ark = settings.image_provider_name == "ark"
    provider = settings.video_provider_name
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
                await asyncio.to_thread(_previs_one, project_id, script_id, shot_id, total, settings)
            _update_generation(generation_id, progress=5 + int(20 * index / max(1, total)), message=f"校准关键帧 {index}/{total}")

        for index, shot_id in enumerate(shot_ids, start=1):
            await asyncio.to_thread(_subtitle_one, project_id, shot_id, settings)
            if provider == "rest":
                await _rest_clip_one(project_id, script_id, shot_id, settings)
            elif provider == "mcp":
                await _mcp_clip_one(project_id, script_id, shot_id, settings)
            elif provider == "seedance":
                await _real_clip_one(project_id, script_id, shot_id, settings)
            else:
                await asyncio.to_thread(_clip_one, project_id, script_id, shot_id, settings)
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
