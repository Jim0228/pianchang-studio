from __future__ import annotations

import asyncio
import io
import json
import re
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from PIL import Image, ImageOps
from sqlalchemy.orm import Session

from .config import Settings
from .db import get_db
from .errors import AppError
from .jobs import regenerate_shot_storyboard, run_previs, run_render
from .models import Asset, Export, Generation, Product, Project, Script, Shot, Skill, Video
from .schemas import (
    ProjectCreate,
    ProjectUpdate,
    ProductUpdate,
    RecommendRequest,
    ScriptCreate,
    ScriptPatch,
    SettingsUpdate,
)
from .services import ffmpeg as ffmpeg_service
from .services import platforms as platform_service
from .services.brief import analyze_product, get_brief, update_product
from .services.packaging import export_bundle
from .services.recommend import recommend_skills
from .services.script import generate_script, patch_script, script_detail
from .services.skill_loader import load_skill_configs
from .utils import json_loads, new_id, sha256_file, truncate

router = APIRouter()

MAX_UPLOAD_BYTES = 15 * 1024 * 1024


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def _project_or_404(db: Session, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise AppError("NOT_FOUND", "项目不存在", status_code=404)
    return project


def _asset_dict(asset: Asset | None) -> dict | None:
    if asset is None:
        return None
    return {
        "id": asset.id,
        "kind": asset.kind,
        "filename": asset.filename,
        "path": asset.path,
        "url": f"/media/{asset.path}",
        "width": asset.width,
        "height": asset.height,
        "duration": asset.duration,
        "meta": json_loads(asset.meta_json, {}),
    }


def _project_dict(project: Project) -> dict:
    return {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "platform": project.platform,
        "duration": project.duration,
        "ratio": project.ratio,
        "status": project.status,
        "created_at": project.created_at.isoformat() if project.created_at else None,
        "updated_at": project.updated_at.isoformat() if project.updated_at else None,
    }


def _product_dict(product: Product | None) -> dict | None:
    if product is None:
        return None
    return {
        "id": product.id,
        "project_id": product.project_id,
        "title": product.title,
        "category": product.category,
        "selling_points": json_loads(product.selling_points_json, []),
        "audience": product.audience,
        "price": product.price,
        "tone": product.tone,
        "brief": get_brief(product),
    }


def _generation_dict(generation: Generation) -> dict:
    return {
        "id": generation.id,
        "project_id": generation.project_id,
        "stage": generation.stage,
        "status": generation.status,
        "progress": generation.progress,
        "message": generation.message,
        "result": json_loads(generation.result_json, {}),
        "error": generation.error,
        "created_at": generation.created_at.isoformat() if generation.created_at else None,
        "updated_at": generation.updated_at.isoformat() if generation.updated_at else None,
    }


def _video_dict(db: Session, video: Video | None) -> dict | None:
    if video is None:
        return None
    cover = db.get(Asset, video.cover_asset_id) if video.cover_asset_id else None
    return {
        "id": video.id,
        "project_id": video.project_id,
        "script_id": video.script_id,
        "path": video.path,
        "url": f"/media/{video.path}",
        "cover": _asset_dict(cover),
        "duration": video.duration,
        "width": video.width,
        "height": video.height,
        "status": video.status,
        "meta": json_loads(video.meta_json, {}),
        "created_at": video.created_at.isoformat() if video.created_at else None,
    }


def _skill_summary(row: Skill) -> dict:
    config = json_loads(row.config_json, {})
    return {
        "id": row.id,
        "name": row.name,
        "category": row.category,
        "goal": row.goal,
        "version": row.version,
        "beats": len(config.get("beats") or []),
        "suitable_categories": config.get("suitable_categories") or [],
        "platforms": config.get("platforms") or [],
        "scenes": (config.get("scenes") or [])[:3],
        "palette": (config.get("prompt") or {}).get("palette") or [],
    }


def _latest_script(db: Session, project_id: str) -> Script | None:
    return (
        db.query(Script)
        .filter(Script.project_id == project_id)
        .order_by(Script.created_at.desc())
        .first()
    )


def _latest_video(db: Session, project_id: str) -> Video | None:
    return (
        db.query(Video)
        .filter(Video.project_id == project_id)
        .order_by(Video.created_at.desc())
        .first()
    )


@router.get("/health")
def health(request: Request, settings: Settings = Depends(get_settings)):
    return {
        "status": "ok",
        "version": settings.version,
        "ai_mode": settings.ai_mode,
        "video_provider": settings.video_provider_name,
        "image_provider": settings.image_provider_name,
        "llm_provider": settings.llm_provider_name,
        "ark_key_present": settings.ark_key_present,
        "mcp_configured": settings.mcp_configured,
        "rest_art_configured": settings.rest_art_configured,
        "deepseek_configured": settings.deepseek_configured,
        "data_dir": str(settings.data_path),
        "ffmpeg": request.app.state.ffmpeg_status,
    }


@router.get("/platforms")
def platforms():
    return platform_service.list_platforms()


@router.get("/stats")
def stats(db: Session = Depends(get_db)):
    return {
        "projects": db.query(Project).count(),
        "videos": db.query(Video).count(),
        "running": db.query(Generation).filter(Generation.status.in_(["queued", "running"])).count(),
        "skills": db.query(Skill).filter(Skill.enabled.is_(True)).count(),
    }


@router.get("/projects")
def list_projects(db: Session = Depends(get_db)):
    projects = db.query(Project).order_by(Project.created_at.desc()).limit(60).all()
    items = []
    for project in projects:
        video = _latest_video(db, project.id)
        cover = db.get(Asset, video.cover_asset_id) if video and video.cover_asset_id else None
        items.append({**_project_dict(project), "cover": _asset_dict(cover), "has_video": video is not None})
    return items


@router.post("/projects")
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)):
    project = Project(
        id=new_id("prj"),
        name=payload.name.strip() or truncate(payload.description.strip(), 20),
        description=payload.description.strip(),
        platform=payload.platform,
        duration=payload.duration,
        ratio=payload.ratio,
        status="draft",
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return _project_dict(project)


@router.get("/projects/{project_id}")
def get_project(project_id: str, db: Session = Depends(get_db)):
    project = _project_or_404(db, project_id)
    product = db.query(Product).filter(Product.project_id == project.id).first()
    script = _latest_script(db, project.id)
    video = _latest_video(db, project.id)
    generation = (
        db.query(Generation)
        .filter(Generation.project_id == project.id)
        .order_by(Generation.created_at.desc())
        .first()
    )
    originals = (
        db.query(Asset)
        .filter(Asset.project_id == project.id, Asset.kind == "original")
        .order_by(Asset.created_at.asc())
        .all()
    )
    return {
        "project": _project_dict(project),
        "product": _product_dict(product),
        "assets": [_asset_dict(asset) for asset in originals],
        "script": script_detail(db, script) if script else None,
        "video": _video_dict(db, video),
        "latest_generation": _generation_dict(generation) if generation else None,
    }


@router.patch("/projects/{project_id}")
def update_project(project_id: str, payload: ProjectUpdate, db: Session = Depends(get_db)):
    project = _project_or_404(db, project_id)
    for key, value in payload.model_dump(exclude_none=True).items():
        setattr(project, key, value)
    db.commit()
    db.refresh(project)
    return _project_dict(project)


@router.post("/projects/{project_id}/upload")
async def upload_assets(
    project_id: str,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    project = _project_or_404(db, project_id)
    folder = settings.data_path / "projects" / project.id / "originals"
    folder.mkdir(parents=True, exist_ok=True)
    created: list[dict] = []

    for upload in files:
        data = await upload.read()
        if len(data) > MAX_UPLOAD_BYTES:
            raise AppError("VALIDATION_ERROR", f"图片 {upload.filename} 超过 15MB", status_code=400)
        try:
            image = Image.open(io.BytesIO(data))
            image = ImageOps.exif_transpose(image)
            if image.mode in ("RGBA", "LA"):
                background = Image.new("RGB", image.size, (255, 255, 255))
                background.paste(image, mask=image.split()[-1])
                image = background
            else:
                image = image.convert("RGB")
        except Exception as exc:  # noqa: BLE001
            raise AppError("VALIDATION_ERROR", f"无法读取图片 {upload.filename}", status_code=400) from exc

        asset_id = new_id("ast")
        path = folder / f"{asset_id}.jpg"
        image.save(path, "JPEG", quality=92)
        thumb = image.copy()
        thumb.thumbnail((480, 480))
        thumb_path = folder / f"{asset_id}_thumb.jpg"
        thumb.save(thumb_path, "JPEG", quality=86)

        asset = Asset(
            id=asset_id,
            project_id=project.id,
            kind="original",
            filename=upload.filename or path.name,
            path=path.relative_to(settings.data_path).as_posix(),
            width=image.width,
            height=image.height,
            sha256=sha256_file(path),
            meta_json=json.dumps(
                {"thumb": thumb_path.relative_to(settings.data_path).as_posix()},
                ensure_ascii=False,
            ),
        )
        db.add(asset)
        project.status = "uploaded"
        db.commit()
        db.refresh(asset)
        created.append(_asset_dict(asset))
    return created


@router.post("/projects/{project_id}/analyze")
async def analyze(project_id: str, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    project = _project_or_404(db, project_id)
    product = await analyze_product(db, project, settings)
    return _product_dict(product)


@router.patch("/products/{product_id}")
def patch_product(product_id: str, payload: ProductUpdate, db: Session = Depends(get_db)):
    product = db.get(Product, product_id)
    if product is None:
        raise AppError("NOT_FOUND", "商品不存在", status_code=404)
    product = update_product(db, product, payload)
    return _product_dict(product)


@router.get("/skills")
def list_skills(db: Session = Depends(get_db)):
    rows = db.query(Skill).filter(Skill.enabled.is_(True)).order_by(Skill.created_at.asc()).all()
    return [_skill_summary(row) for row in rows]


@router.get("/skills/{skill_id}")
def get_skill(skill_id: str, db: Session = Depends(get_db)):
    row = db.get(Skill, skill_id)
    if row is None:
        raise AppError("NOT_FOUND", "风格不存在", status_code=404)
    config = json_loads(row.config_json, {})
    return {**_skill_summary(row), "config": config}


@router.post("/skills/recommend")
def recommend(payload: RecommendRequest, db: Session = Depends(get_db)):
    project = _project_or_404(db, payload.project_id)
    product = db.query(Product).filter(Product.project_id == project.id).first()
    if product is None:
        raise AppError("VALIDATION_ERROR", "请先解析商品资料", status_code=409)
    brief = get_brief(product)
    return {"items": recommend_skills(db, project, brief, goal="转化", top_n=3)}


@router.post("/projects/{project_id}/scripts")
async def create_script(
    project_id: str,
    payload: ScriptCreate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    project = _project_or_404(db, project_id)
    product = db.query(Product).filter(Product.project_id == project.id).first()
    if product is None:
        raise AppError("VALIDATION_ERROR", "请先解析商品资料", status_code=409)
    skill = db.get(Skill, payload.skill_id)
    if skill is None:
        raise AppError("NOT_FOUND", "视频风格不存在", status_code=404)
    script = await generate_script(
        db,
        project,
        product,
        skill,
        payload.platform or project.platform,
        payload.duration or project.duration,
        payload.ratio or project.ratio,
        settings,
    )
    return script_detail(db, script)


@router.get("/scripts/{script_id}")
def get_script(script_id: str, db: Session = Depends(get_db)):
    script = db.get(Script, script_id)
    if script is None:
        raise AppError("NOT_FOUND", "脚本不存在", status_code=404)
    return script_detail(db, script)


@router.patch("/scripts/{script_id}")
def edit_script(script_id: str, payload: ScriptPatch, db: Session = Depends(get_db)):
    script = db.get(Script, script_id)
    if script is None:
        raise AppError("NOT_FOUND", "脚本不存在", status_code=404)
    return patch_script(db, script, payload)


@router.post("/scripts/{script_id}/previs")
async def start_previs(
    script_id: str,
    force: bool = False,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    script = db.get(Script, script_id)
    if script is None:
        raise AppError("NOT_FOUND", "脚本不存在", status_code=404)
    if force and settings.image_provider_name == "rest" and not settings.rest_art_configured:
        raise AppError("REST_NOT_CONFIGURED", "重新生成分镜图需要配置 REST_ART_BASE_URL / REST_ART_TOKEN", status_code=400)
    generation = Generation(id=new_id("gen"), project_id=script.project_id, stage="previs", status="queued")
    db.add(generation)
    db.commit()
    db.refresh(generation)
    if settings.sync_jobs:
        await run_previs(generation.id, script.id, settings, force=force)
    else:
        asyncio.create_task(run_previs(generation.id, script.id, settings, force=force))
    db.refresh(generation)
    return _generation_dict(generation)


@router.post("/scripts/{script_id}/render")
async def start_render(
    script_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    script = db.get(Script, script_id)
    if script is None:
        raise AppError("NOT_FOUND", "脚本不存在", status_code=404)
    provider = settings.video_provider_name
    if provider == "rest" and not settings.rest_art_configured:
        raise AppError("REST_NOT_CONFIGURED", "请先配置 REST_ART_BASE_URL 与 REST_ART_TOKEN", status_code=400)
    if provider == "mcp" and not settings.mcp_configured:
        raise AppError("MCP_NOT_CONFIGURED", "请先配置 MCP_VIDEO_URL 与 MCP_VIDEO_TOKEN", status_code=400)
    if provider == "seedance" and not settings.ark_key_present:
        raise AppError("ARK_KEY_MISSING", "Seedance 需要配置 ARK_API_KEY", status_code=400)
    if settings.image_provider_name == "rest" and not settings.rest_art_configured:
        raise AppError("REST_NOT_CONFIGURED", "图片提供方为 REST，但未配置 REST_ART_BASE_URL / REST_ART_TOKEN", status_code=400)
    generation = Generation(id=new_id("gen"), project_id=script.project_id, stage="render", status="queued")
    db.add(generation)
    db.commit()
    db.refresh(generation)
    if settings.sync_jobs:
        await run_render(generation.id, script.id, settings)
    else:
        asyncio.create_task(run_render(generation.id, script.id, settings))
    db.refresh(generation)
    return _generation_dict(generation)


@router.post("/shots/{shot_id}/regenerate")
async def regenerate(
    shot_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    shot = db.get(Shot, shot_id)
    if shot is None:
        raise AppError("NOT_FOUND", "镜头不存在", status_code=404)
    script = db.get(Script, shot.script_id)
    if script is None:
        raise AppError("NOT_FOUND", "脚本不存在", status_code=404)
    if settings.image_provider_name == "rest" and not settings.rest_art_configured:
        raise AppError("REST_NOT_CONFIGURED", "重新生成分镜图需要配置 REST_ART_BASE_URL / REST_ART_TOKEN", status_code=400)
    generation = Generation(id=new_id("gen"), project_id=script.project_id, stage="previs", status="queued")
    db.add(generation)
    db.commit()
    db.refresh(generation)
    if settings.sync_jobs:
        await regenerate_shot_storyboard(generation.id, shot_id, settings)
    else:
        asyncio.create_task(regenerate_shot_storyboard(generation.id, shot_id, settings))
    db.refresh(generation)
    return _generation_dict(generation)


@router.get("/generations/{generation_id}")
def get_generation(generation_id: str, db: Session = Depends(get_db)):
    generation = db.get(Generation, generation_id)
    if generation is None:
        raise AppError("NOT_FOUND", "任务不存在", status_code=404)
    return _generation_dict(generation)


@router.get("/tasks")
def list_tasks(db: Session = Depends(get_db)):
    rows = (
        db.query(Generation, Project.name)
        .join(Project, Project.id == Generation.project_id)
        .order_by(Generation.created_at.desc())
        .limit(50)
        .all()
    )
    return [{**_generation_dict(generation), "project_name": name} for generation, name in rows]


@router.get("/projects/{project_id}/video")
def get_video(project_id: str, db: Session = Depends(get_db)):
    _project_or_404(db, project_id)
    return _video_dict(db, _latest_video(db, project_id))


@router.post("/videos/{video_id}/export")
def create_export(video_id: str, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    video = db.get(Video, video_id)
    if video is None:
        raise AppError("NOT_FOUND", "成片不存在", status_code=404)
    export = export_bundle(db, settings, video)
    path = settings.data_path / export.path
    return {
        "id": export.id,
        "download_url": f"/api/exports/{export.id}/download",
        "filename": path.name,
        "size": path.stat().st_size if path.exists() else 0,
    }


@router.get("/videos/{video_id}/download")
def download_video(
    video_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    video = db.get(Video, video_id)
    if video is None:
        raise AppError("NOT_FOUND", "成片不存在", status_code=404)
    path = settings.data_path / video.path
    if not path.exists():
        raise AppError("NOT_FOUND", "成片文件已丢失", status_code=404)
    project = db.get(Project, video.project_id)
    name = re.sub(r'[\\/:*?"<>|]+', "_", (project.name if project else "AI视频") or "AI视频")
    return FileResponse(path, filename=f"{name}_1080x1920.mp4", media_type="video/mp4")


@router.get("/exports/{export_id}/download")
def download_export(export_id: str, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    export = db.get(Export, export_id)
    if export is None:
        raise AppError("NOT_FOUND", "导出不存在", status_code=404)
    path = settings.data_path / export.path
    if not path.exists():
        raise AppError("NOT_FOUND", "导出文件已丢失", status_code=404)
    project = db.get(Project, export.project_id)
    name = re.sub(r'[\\/:*?"<>|]+', "_", (project.name if project else "AI视频") or "AI视频")
    return FileResponse(path, filename=f"{name}_AI视频.zip", media_type="application/zip")


@router.get("/settings")
def get_settings_api(request: Request, settings: Settings = Depends(get_settings), db: Session = Depends(get_db)):
    return {
        "app_name": settings.app_name,
        "version": settings.version,
        "ai_mode": settings.ai_mode,
        "video_provider": settings.video_provider_name,
        "image_provider": settings.image_provider_name,
        "llm_provider": settings.llm_provider_name,
        "ark_key_present": settings.ark_key_present,
        "mcp_configured": settings.mcp_configured,
        "rest_art_configured": settings.rest_art_configured,
        "deepseek_configured": settings.deepseek_configured,
        "mcp_video_url": settings.mcp_video_url,
        "mcp_video_resolution": settings.mcp_video_resolution,
        "rest_art_base_url": settings.rest_art_base_url,
        "rest_image_params": settings.rest_image_params,
        "models": {
            "chat": settings.ark_chat_model,
            "vision": settings.ark_vision_model,
            "image": settings.ark_image_model,
            "video": settings.ark_video_model,
        },
        "data_dir": str(settings.data_path),
        "video": {"width": settings.video_width, "height": settings.video_height, "fps": settings.fps},
        "sync_jobs": settings.sync_jobs,
        "ffmpeg": request.app.state.ffmpeg_status,
        "skills": db.query(Skill).filter(Skill.enabled.is_(True)).count(),
    }


@router.get("/mcp/status")
async def mcp_status(settings: Settings = Depends(get_settings)):
    if not settings.mcp_configured:
        return {"ok": False, "configured": False, "error": "未配置 MCP_VIDEO_URL / MCP_VIDEO_TOKEN"}
    from .services.providers.mcp import McpClient

    try:
        result = await McpClient(settings).ping()
        return {"ok": True, "configured": True, **result}
    except AppError as exc:
        return {"ok": False, "configured": True, "error": exc.message}


@router.get("/rest/status")
async def rest_status(settings: Settings = Depends(get_settings)):
    if not settings.rest_art_configured:
        return {"ok": False, "configured": False, "error": "未配置 REST_ART_BASE_URL / REST_ART_TOKEN"}
    from .services.providers.rest_art import RestArtClient

    try:
        result = await RestArtClient(settings).ping()
        return {"ok": True, "configured": True, **result}
    except AppError as exc:
        return {"ok": False, "configured": True, "error": exc.message}


@router.put("/settings")
def update_settings_api(payload: SettingsUpdate, request: Request, settings: Settings = Depends(get_settings)):
    if payload.ai_mode is not None:
        settings.ai_mode = payload.ai_mode
    if payload.llm_provider is not None:
        if payload.llm_provider == "deepseek" and not settings.deepseek_configured:
            raise AppError("DEEPSEEK_KEY_MISSING", "切换到 DeepSeek 前，请先在 .env 配置 DEEPSEEK_API_KEY", status_code=400)
        if payload.llm_provider == "ark" and not settings.ark_key_present:
            raise AppError("ARK_KEY_MISSING", "Ark LLM 需要配置 ARK_API_KEY", status_code=400)
        settings.llm_provider = payload.llm_provider
    if payload.image_provider is not None:
        if payload.image_provider == "rest" and not settings.rest_art_configured:
            raise AppError("REST_NOT_CONFIGURED", "REST 图片需要配置 REST_ART_BASE_URL / REST_ART_TOKEN", status_code=400)
        if payload.image_provider == "ark" and not settings.ark_key_present:
            raise AppError("ARK_KEY_MISSING", "Ark 图片需要配置 ARK_API_KEY", status_code=400)
        settings.image_provider = payload.image_provider
    if payload.video_provider is not None:
        if payload.video_provider == "rest" and not settings.rest_art_configured:
            raise AppError("REST_NOT_CONFIGURED", "REST 视频需要配置 REST_ART_BASE_URL / REST_ART_TOKEN", status_code=400)
        if payload.video_provider == "mcp" and not settings.mcp_configured:
            raise AppError("MCP_NOT_CONFIGURED", "切换 MCP 视频前，请先在 .env 配置 MCP_VIDEO_URL 与 MCP_VIDEO_TOKEN", status_code=400)
        if payload.video_provider == "seedance" and not settings.ark_key_present:
            raise AppError("ARK_KEY_MISSING", "Seedance 需要配置 ARK_API_KEY", status_code=400)
        settings.video_provider = payload.video_provider
    request.app.state.settings = settings
    return {
        "ai_mode": settings.ai_mode,
        "video_provider": settings.video_provider_name,
        "image_provider": settings.image_provider_name,
        "llm_provider": settings.llm_provider_name,
        "ark_key_present": settings.ark_key_present,
        "mcp_configured": settings.mcp_configured,
        "rest_art_configured": settings.rest_art_configured,
        "deepseek_configured": settings.deepseek_configured,
    }
