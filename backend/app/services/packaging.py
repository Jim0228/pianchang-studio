from __future__ import annotations

import json
import shutil
import zipfile
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from ..config import Settings
from ..errors import AppError
from ..models import Asset, Export, Product, Project, Script, Video
from ..utils import json_dumps, json_loads, new_id, srt_time
from .script import script_detail


def write_srt(detail: dict, path: Path) -> None:
    lines: list[str] = []
    cursor = 0.0
    for index, shot in enumerate(detail.get("shots", []), start=1):
        seconds = float(shot.get("seconds", 3))
        start = cursor
        end = cursor + seconds
        text = (shot.get("on_screen") or shot.get("voiceover") or "").strip()
        lines.append(f"{index}\n{srt_time(start)} --> {srt_time(end)}\n{text}\n")
        cursor = end
    path.write_text("\n".join(lines), encoding="utf-8")


def write_caption(detail: dict, path: Path) -> None:
    caption = detail.get("caption") or {}
    hashtags = " ".join(caption.get("hashtags") or [])
    shots = detail.get("shots") or []
    content = [
        f"# {caption.get('title') or detail.get('title', '')}",
        "",
        caption.get("body", ""),
        "",
        hashtags,
        "",
        "## 分镜口播",
        "",
    ]
    for shot in shots:
        content.append(f"{shot.get('idx')}. [{shot.get('beat')}] {shot.get('voiceover', '')}")
    content += [
        "",
        "---",
        f"风格：{detail.get('skill_id', '')} v{detail.get('skill_version', '')}",
        f"平台：{detail.get('platform', '')} / {detail.get('ratio', '')} / {detail.get('duration', '')}s",
        "",
        "> 本内容由 AI 辅助生成，已标注「AI 生成」。",
    ]
    path.write_text("\n".join(content), encoding="utf-8")


def build_manifest(
    settings: Settings,
    project: Project,
    product: Product | None,
    script: Script,
    video: Video,
    detail: dict,
) -> dict:
    brief = json_loads(product.brief_json, {}) if product else {}
    return {
        "manifest_version": 1,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "project": {
            "id": project.id,
            "name": project.name,
            "platform": project.platform,
            "duration": project.duration,
        },
        "product": {
            "title": brief.get("title", ""),
            "category": brief.get("category", ""),
            "selling_points": brief.get("selling_points", []),
        },
        "skill": {
            "id": script.skill_id,
            "version": detail.get("skill_version", script.version),
        },
        "script": {
            "id": script.id,
            "version": script.version,
            "duration": script.duration,
            "ratio": script.ratio,
            "shots": [
                {
                    "idx": shot.get("idx"),
                    "seconds": shot.get("seconds"),
                    "beat": shot.get("beat"),
                    "voiceover": shot.get("voiceover"),
                    "on_screen": shot.get("on_screen"),
                }
                for shot in detail.get("shots", [])
            ],
        },
        "video": {
            "id": video.id,
            "path": video.path,
            "width": video.width,
            "height": video.height,
            "duration": video.duration,
        },
        "mode": settings.ai_mode,
        "video_provider": settings.video_provider_name,
        "models": {
            "chat": settings.ark_chat_model,
            "vision": settings.ark_vision_model,
            "image": settings.ark_image_model,
            "video": settings.ark_video_model,
        },
        "ai": {
            "generated": True,
            "label": "AI 生成",
            "disclosure": "本视频由人工智能生成或辅助生成，画面为演示/创意合成，不代表真实拍摄。",
        },
        "compliance": detail.get("compliance", []),
    }


def export_bundle(session: Session, settings: Settings, video: Video) -> Export:
    project = session.get(Project, video.project_id)
    script = session.get(Script, video.script_id)
    if project is None or script is None:
        raise AppError("NOT_FOUND", "项目或脚本不存在", status_code=404)
    product = session.query(Product).filter(Product.project_id == project.id).first()
    detail = script_detail(session, script)

    export_id = new_id("exp")
    root = settings.data_path / "projects" / project.id / "exports" / export_id
    root.mkdir(parents=True, exist_ok=True)

    video_path = settings.data_path / video.path
    if not video_path.exists():
        raise AppError("MEDIA_ERROR", "成片文件不存在，请重新生成", status_code=409)
    shutil.copy2(video_path, root / "final.mp4")

    cover_path = None
    if video.cover_asset_id:
        cover_asset = session.get(Asset, video.cover_asset_id)
        if cover_asset and (settings.data_path / cover_asset.path).exists():
            cover_path = settings.data_path / cover_asset.path
            shutil.copy2(cover_path, root / "cover.jpg")

    (root / "script.json").write_text(
        json.dumps(json_loads(script.script_json, {}), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_srt(detail, root / "subtitles.srt")
    write_caption(detail, root / "caption.md")
    manifest = build_manifest(settings, project, product, script, video, detail)
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    zip_path = settings.data_path / "projects" / project.id / "exports" / f"{export_id}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(root.rglob("*")):
            if file.is_file():
                zf.write(file, file.relative_to(root).as_posix())

    export = Export(
        id=export_id,
        project_id=project.id,
        video_id=video.id,
        path=zip_path.relative_to(settings.data_path).as_posix(),
        manifest_json=json_dumps(manifest),
    )
    session.add(export)
    session.commit()
    session.refresh(export)
    return export
