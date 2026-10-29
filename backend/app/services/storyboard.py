from __future__ import annotations

import io
import json
import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import Asset, Project, Script, Shot
from ..utils import ensure_parent, get_font, json_dumps, new_id, sha256_file
from .providers.rest_art import RestArtClient

logger = logging.getLogger(__name__)

# 分镜图是"商品一致性的第一道闸门"：先固定身份，再谈场景与风格。
IDENTITY_LOCK = (
    "PRODUCT IDENTITY LOCK — the product must stay exactly identical to the reference image. "
    "Keep shape, proportions, color, material, label, logo, display and every part unchanged. "
    "Do not restyle, recolor, resize, mirror, or add/remove parts."
)

FALLBACK_NEGATIVE = (
    "no text, no letters, no numbers, no price tag, no watermark, no badge, "
    "no extra products, no duplicated product, no human faces, no hands, no deformed label"
)
REVIEW_CHECKLIST = [
    "对比参考图：外形、比例、颜色、材质是否一致",
    "标签/屏幕/Logo 的位置与内容是否被改写",
    "是否出现多余商品、人物、手部、文字或水印",
    "五张分镜的光线、色温、景别是否能连续剪接",
    "构图是否给字幕留出安全区（下方 20%）",
]


def _product_visual_text(brief: dict, description: str = "") -> str:
    parts: list[str] = []
    visual = brief.get("product_visual")
    if isinstance(visual, dict) and visual:
        parts.append("; ".join(f"{key}: {value}" for key, value in visual.items() if value))
    elif isinstance(visual, str) and visual.strip():
        parts.append(visual.strip())
    else:
        title = str(brief.get("title") or "the product").strip()
        category = str(brief.get("category") or "").strip()
        parts.append(f"{title} ({category})" if category else title)
    clean_description = " ".join(str(description or "").split())[:120]
    if clean_description:
        parts.append(f"as described by the seller: {clean_description}")
    return "; ".join(parts)


def _merge_negative(skill_negative: str, *, allow_people: bool = False) -> str:
    """商品一致性约束不依赖 Skill；有人物出镜时放行人物，但仍禁止多余的人和文字。"""
    hard = [
        "no text",
        "no letters",
        "no price tag",
        "no watermark",
        "no extra products",
        "no second product",
        "no duplicate product",
    ]
    if allow_people:
        hard += ["no extra people", "no duplicate person"]
    else:
        hard += ["no people", "no hands"]
    merged = [item.strip() for item in (skill_negative or "").split(",") if item.strip()]
    lowered = {item.lower() for item in merged}
    for item in hard:
        if item.lower() not in lowered:
            merged.append(item)
    return ", ".join(merged)


def resolve_scene_bible(skill_config: dict) -> dict:
    """把风格的场景基准固化成一份全片共享的"场景圣经"，保证每张分镜是同一个空间。"""
    raw = skill_config.get("scene_bible") or {}
    prompt_cfg = skill_config.get("prompt") or {}
    props = raw.get("props") or []
    return {
        "location": str(raw.get("location") or "干净、无杂物的商品拍摄场景"),
        "time_of_day": str(raw.get("time_of_day") or "daytime"),
        "props": [str(item) for item in props],
        "lighting": str(raw.get("lighting") or prompt_cfg.get("lighting") or "柔和自然光"),
        "grade": str(raw.get("grade") or "自然中性色，低饱和"),
        "lens": str(raw.get("lens") or "35mm 等效，浅景深"),
    }


def _scene_bible_lines(scene_bible: dict) -> list[str]:
    lines = [
        "SCENE BIBLE — the same physical space in every shot of this video:",
        f"Location: {scene_bible['location']}.",
        f"Time of day: {scene_bible['time_of_day']}.",
    ]
    if scene_bible.get("props"):
        lines.append("Fixed props: " + ", ".join(scene_bible["props"]) + ".")
    lines += [
        f"Lighting: {scene_bible['lighting']}.",
        f"Color grade: {scene_bible['grade']}.",
        f"Lens: {scene_bible['lens']}.",
    ]
    return lines


def resolve_presenter(skill_config: dict) -> dict:
    raw = skill_config.get("presenter") or {}
    return {
        "enabled": bool(raw.get("enabled")),
        "type": str(raw.get("type") or "none"),
        "description": str(raw.get("description") or "").strip(),
        "framing": str(raw.get("framing") or "").strip(),
    }


def resolve_continuity(skill_config: dict) -> dict:
    raw = skill_config.get("continuity") or {}
    return {
        "mode": str(raw.get("mode") or "cut"),
        "action_arc": [str(item) for item in (raw.get("action_arc") or [])],
        "rules": [str(item) for item in (raw.get("rules") or [])],
    }


def _presenter_lines(presenter: dict) -> list[str]:
    if not presenter.get("enabled") or not presenter.get("description"):
        return []
    lines = [
        "SAME PERSON IN EVERY SHOT (identity lock): " + presenter["description"] + ".",
    ]
    if presenter.get("framing"):
        lines.append(f"Presenter framing: {presenter['framing']}.")
    return lines


def _continuity_lines(continuity: dict) -> list[str]:
    lines: list[str] = []
    if continuity.get("rules"):
        lines.append("CONTINUITY RULES: " + " ".join(continuity["rules"]))
    if continuity.get("action_arc"):
        numbered = " → ".join(
            f"{index}) {step}" for index, step in enumerate(continuity["action_arc"], start=1)
        )
        lines.append(f"ACTION ARC (each shot continues the previous): {numbered}.")
    return lines


def build_storyboard_prompt(
    shot: Shot,
    brief: dict,
    skill_config: dict,
    ratio: str = "9:16",
    description: str = "",
    scene_bible: dict | None = None,
    has_scene_anchor: bool = False,
    has_previous: bool = False,
) -> str:
    """镜头级分镜图 prompt：商品锁定 + 人物锁定 + 场景圣经 + 动作连贯 + 反向词。"""
    style = skill_config.get("prompt") or {}
    presenter = resolve_presenter(skill_config)
    continuity = resolve_continuity(skill_config)
    negative = _merge_negative(
        str(style.get("negative") or ""), allow_people=presenter.get("enabled", False)
    )
    direction = (shot.prompt or "").strip()
    scene = (shot.scene or shot.action or "").strip()
    camera = (shot.camera or "static mid-shot").strip()
    product = _product_visual_text(brief, description)
    bible = scene_bible or resolve_scene_bible(skill_config)
    lines = [
        IDENTITY_LOCK,
        f"Product: {product}.",
        "Exactly one unit of this product is in frame. Match the reference image: same shape, "
        "same color variant, same screen, same head, same logo and proportions.",
        str(style.get("style") or "Photorealistic e-commerce commercial photography").strip(),
        *_scene_bible_lines(bible),
        *_presenter_lines(presenter),
        *_continuity_lines(continuity),
        f"Shot scene: {scene}.",
        f"Camera: {camera}.",
    ]
    if has_scene_anchor:
        lines.append(
            "The second reference image is the empty scene anchor: keep its location, props, "
            "lighting and color grade exactly; place the product and person into that same space."
        )
    if has_previous:
        lines.append(
            "The third reference image is the previous shot: keep the same person, same outfit, "
            "same props and lighting, and continue the action from where the previous shot ended. "
            "Do not reset the pose or move the set."
        )
    palette = style.get("palette") or []
    if palette:
        lines.append("Color palette accents: " + ", ".join(str(item) for item in palette) + ".")
    framing = (
        "the product occupies 40-60% of the frame"
        if not presenter.get("enabled")
        else "the person and the product share the frame, product stays readable"
    )
    lines.append(
        f"Framing: {ratio} vertical, {framing}. "
        "The frame contains only the product, the person (if any) and the fixed props — nothing else."
    )
    lines.append("The frame must be text-free: no text, no letters, no numbers, no captions.")
    if direction:
        lines.append(f"Direction: {direction}")
    lines.append(f"Avoid: {negative}.")
    return "\n".join(lines)


def build_scene_anchor_prompt(
    skill_config: dict, scene_bible: dict, ratio: str = "9:16"
) -> str:
    """场景基准图：只拍空场景（无商品、无人物），作为所有分镜的环境参考。"""
    style = skill_config.get("prompt") or {}
    negative = _merge_negative(str(style.get("negative") or ""))
    lines = [
        "Empty establishing shot of a product-video set. NO product, NO people, NO hands.",
        str(style.get("style") or "Photorealistic e-commerce commercial photography").strip(),
        *_scene_bible_lines(scene_bible),
        f"Framing: {ratio} vertical, uncluttered, soft depth of field, negative space in the middle "
        "where the product will later be placed.",
        "Text-free: no text, no letters, no watermark, no logo.",
        f"Avoid: {negative}.",
    ]
    return "\n".join(lines)


def build_video_prompt(shot: Shot, skill_config: dict) -> str:
    """图生视频 prompt：首帧固定，人物与动作延续，只允许轻微运镜与光线变化。"""
    style = skill_config.get("prompt") or {}
    presenter = resolve_presenter(skill_config)
    continuity = resolve_continuity(skill_config)
    camera = (shot.camera or "slow push-in").strip()
    action = (shot.action or shot.scene or "").strip()
    lines = [
        "Use the first provided image as the exact first frame and keep its composition.",
        f"Animate a subtle {camera} and a gentle natural light shift over the clip.",
        "Keep the product's shape, proportions, color, material, label, logo and screen unchanged; "
        "no morphing, no extra objects, no text, no watermark.",
    ]
    if presenter.get("enabled"):
        lines.append(
            "Keep the same person, face, hair, outfit and body proportions as the first frame; "
            "only natural micro-movements and the described action, no identity drift."
        )
    else:
        lines.append("No people and no hands in frame.")
    if continuity.get("mode") == "continuous_action" and action:
        lines.append(f"The action continues naturally from the first frame: {action}.")
    if str(style.get("style") or "").strip():
        lines.append(str(style["style"]).strip())
    lines.append("Photorealistic e-commerce commercial quality.")
    return " ".join(lines)


def _originals(session: Session, project_id: str, limit: int = 2) -> list[Path]:
    rows = (
        session.query(Asset)
        .filter(Asset.project_id == project_id, Asset.kind == "original")
        .order_by(Asset.created_at.asc())
        .limit(limit)
        .all()
    )
    return [Path(row.path) for row in rows]


async def ensure_scene_anchor(
    session: Session,
    settings: Settings,
    project: Project,
    script: Script,
    skill_config: dict,
    *,
    force: bool = False,
) -> Asset | None:
    """生成/复用一个"空场景"基准图，作为全片所有分镜的环境参考。"""
    filename = f"scene_anchor_{script.skill_id}.jpg"
    existing = (
        session.query(Asset)
        .filter(
            Asset.project_id == project.id,
            Asset.kind == "scene",
            Asset.filename == filename,
        )
        .first()
    )
    if existing is not None and not force and (settings.data_path / existing.path).exists():
        return existing

    scene_bible = resolve_scene_bible(skill_config)
    prompt = build_scene_anchor_prompt(skill_config, scene_bible, script.ratio)
    params = dict(settings.rest_image_params_dict)
    params.setdefault("aspectRatio", "portrait")

    client = RestArtClient(settings)
    blob, job = await client.generate_image(prompt, [], params=params)
    image = Image.open(io.BytesIO(blob)).convert("RGB")
    image = ImageOps.fit(image, (settings.video_width, settings.video_height), method=Image.LANCZOS)
    path = settings.data_path / "projects" / project.id / "storyboards" / filename
    ensure_parent(path)
    image.save(path, "JPEG", quality=94)

    asset = Asset(
        id=new_id("ast"),
        project_id=project.id,
        kind="scene",
        filename=path.name,
        path=path.relative_to(settings.data_path).as_posix(),
        width=settings.video_width,
        height=settings.video_height,
        sha256=sha256_file(path),
        meta_json=json_dumps(
            {
                "mode": "rest_image",
                "job_id": str(job.get("job_id") or ""),
                "prompt": prompt,
                "scene_bible": scene_bible,
            }
        ),
    )
    session.add(asset)
    session.commit()
    session.refresh(asset)
    return asset


async def generate_storyboard(
    session: Session,
    settings: Settings,
    project: Project,
    script: Script,
    shot: Shot,
    skill_config: dict,
    brief: dict,
    *,
    force: bool = False,
) -> Asset:
    """用 REST 图片模型产出无文字分镜图，并作为该镜头的首帧写回 shot.keyframe_asset_id。

    force=False 时复用已生成的分镜图（渲染阶段不再重复花钱）；用户点「重新生成」时 force=True。
    """
    filename = f"shot_{shot.idx:02d}.jpg"
    existing = (
        session.query(Asset)
        .filter(
            Asset.project_id == project.id,
            Asset.kind == "storyboard",
            Asset.filename == filename,
        )
        .first()
    )
    if (
        existing is not None
        and not force
        and shot.keyframe_asset_id == existing.id
        and (settings.data_path / existing.path).exists()
    ):
        shot.status = "storyboard_ready"
        session.commit()
        return existing

    scene_bible = resolve_scene_bible(skill_config)
    anchor: Asset | None = None
    if settings.image_provider_name == "rest":
        try:
            anchor = await ensure_scene_anchor(session, settings, project, script, skill_config)
        except Exception as exc:  # noqa: BLE001
            logger.warning("场景基准图生成失败，继续只带商品参考图：%s", exc)

    reference_paths = [settings.data_path / path for path in _originals(session, project.id)]
    reference_paths = [path for path in reference_paths if path.exists()]
    if anchor is not None:
        anchor_path = settings.data_path / anchor.path
        if anchor_path.exists():
            reference_paths.append(anchor_path)

    previous_asset: Asset | None = None
    if shot.idx > 1:
        previous_shot = (
            session.query(Shot)
            .filter(Shot.script_id == script.id, Shot.idx == shot.idx - 1)
            .first()
        )
        if previous_shot is not None and previous_shot.keyframe_asset_id:
            candidate = session.get(Asset, previous_shot.keyframe_asset_id)
            if candidate is not None and (settings.data_path / candidate.path).exists():
                previous_asset = candidate
                reference_paths.append(settings.data_path / candidate.path)

    prompt = build_storyboard_prompt(
        shot,
        brief,
        skill_config,
        script.ratio,
        project.description or "",
        scene_bible=scene_bible,
        has_scene_anchor=anchor is not None,
        has_previous=previous_asset is not None,
    )
    params = dict(settings.rest_image_params_dict)
    params.setdefault("aspectRatio", "portrait")

    client = RestArtClient(settings)
    blob, job = await client.generate_image(prompt, reference_paths, params=params)

    image = Image.open(io.BytesIO(blob)).convert("RGB")
    image = ImageOps.fit(
        image,
        (settings.video_width, settings.video_height),
        method=Image.LANCZOS,
    )
    path = settings.data_path / "projects" / project.id / "storyboards" / filename
    ensure_parent(path)
    image.save(path, "JPEG", quality=94)

    meta_json = json_dumps(
        {
            "shot_idx": shot.idx,
            "mode": "rest_image",
            "job_id": str(job.get("job_id") or ""),
            "model": job.get("model"),
            "prompt": prompt,
            "baked_text": False,
            "review": "pending",
            "scene_bible": scene_bible,
            "scene_anchor": anchor.path if anchor is not None else None,
            "previous_shot": previous_asset.path if previous_asset is not None else None,
        }
    )
    if existing is not None:
        # 同一条镜头复用同一行资产，避免"重新生成"在库里堆出重复记录
        asset = existing
        asset.path = path.relative_to(settings.data_path).as_posix()
        asset.width = settings.video_width
        asset.height = settings.video_height
        asset.sha256 = sha256_file(path)
        asset.meta_json = meta_json
    else:
        asset = Asset(
            id=new_id("ast"),
            project_id=project.id,
            kind="storyboard",
            filename=path.name,
            path=path.relative_to(settings.data_path).as_posix(),
            width=settings.video_width,
            height=settings.video_height,
            sha256=sha256_file(path),
            meta_json=meta_json,
        )
        session.add(asset)
        session.flush()
    shot.keyframe_asset_id = asset.id
    shot.status = "storyboard_ready"
    shot.error = ""
    session.commit()
    return asset


def _contact_sheet(
    paths: list[Path],
    reference: Path | None,
    title: str,
    out_path: Path,
    anchor: Path | None = None,
) -> None:
    thumb_w, thumb_h = 360, 640
    margin, label_h = 24, 54
    items: list[tuple[Path, str]] = []
    if reference is not None:
        items.append((reference, "REFERENCE"))
    if anchor is not None:
        items.append((anchor, "SCENE"))
    items += [(path, f"SHOT {index + 1:02d}") for index, path in enumerate(paths)]
    cols = 3
    rows = max(1, (len(items) + cols - 1) // cols)
    width = cols * thumb_w + (cols + 1) * margin
    height = 120 + rows * (thumb_h + label_h) + (rows + 1) * margin
    canvas = Image.new("RGB", (width, height), "#1d1712")
    draw = ImageDraw.Draw(canvas)
    draw.text((margin, 34), f"STORYBOARD · {title}", font=get_font(30, bold=True), fill=(255, 255, 255))
    draw.text(
        (margin, 76),
        "REFERENCE / SCENE anchor first, then SHOT 01..N — 逐镜核对商品与场景一致性",
        font=get_font(14),
        fill=(190, 178, 160),
    )
    for index, (path, label) in enumerate(items):
        row, col = divmod(index, cols)
        x = margin + col * (thumb_w + margin)
        y = 120 + margin + row * (thumb_h + label_h + margin)
        image = ImageOps.fit(Image.open(path).convert("RGB"), (thumb_w, thumb_h), method=Image.LANCZOS)
        canvas.paste(image, (x, y))
        draw.rectangle([x, y + thumb_h, x + thumb_w, y + thumb_h + label_h], fill="#2b211b")
        draw.text((x + 12, y + thumb_h + 12), label, font=get_font(18), fill=(255, 224, 138))
    ensure_parent(out_path)
    canvas.save(out_path, "JPEG", quality=92)


def build_storyboard_review(
    session: Session,
    settings: Settings,
    project: Project,
    script: Script,
    skill_config: dict,
    brief: dict,
) -> dict:
    """把分镜图合成联系表并写 review.json，供人工审核与留档。"""
    shots = session.query(Shot).filter(Shot.script_id == script.id).order_by(Shot.idx.asc()).all()
    assets = {
        asset.id: asset
        for asset in session.query(Asset)
        .filter(Asset.project_id == project.id, Asset.kind == "storyboard")
        .all()
    }
    rows: list[dict] = []
    paths: list[Path] = []
    for shot in shots:
        asset = assets.get(shot.keyframe_asset_id or "")
        path = settings.data_path / asset.path if asset else None
        if asset is None or path is None or not path.exists():
            rows.append({"idx": shot.idx, "file": None, "status": "missing"})
            continue
        meta = json.loads(asset.meta_json or "{}")
        paths.append(path)
        rows.append(
            {
                "idx": shot.idx,
                "file": asset.path,
                "width": asset.width,
                "height": asset.height,
                "status": "pending_manual_review",
                "prompt": meta.get("prompt", ""),
                "job_id": meta.get("job_id", ""),
            }
        )

    originals = _originals(session, project.id, limit=1)
    reference = settings.data_path / originals[0] if originals else None
    if reference is not None and not reference.exists():
        reference = None

    anchor_asset = (
        session.query(Asset)
        .filter(
            Asset.project_id == project.id,
            Asset.kind == "scene",
            Asset.filename == f"scene_anchor_{script.skill_id}.jpg",
        )
        .first()
    )
    anchor = settings.data_path / anchor_asset.path if anchor_asset else None
    if anchor is not None and not anchor.exists():
        anchor = None

    folder = settings.data_path / "projects" / project.id / "storyboards"
    contact = folder / "contact_sheet.jpg"
    if paths:
        _contact_sheet(
            paths, reference, skill_config.get("name", script.skill_id), contact, anchor=anchor
        )

    review = {
        "script_id": script.id,
        "reference": reference.relative_to(settings.data_path).as_posix() if reference else None,
        "scene_anchor": anchor.relative_to(settings.data_path).as_posix() if anchor else None,
        "scene_bible": resolve_scene_bible(skill_config),
        "contact_sheet": contact.relative_to(settings.data_path).as_posix() if paths else None,
        "normalization": f"{settings.video_width}x{settings.video_height} center-crop",
        "shots": rows,
        "checklist": REVIEW_CHECKLIST,
        "automated_note": "自动检查只验证尺寸与文件存在；商品与场景一致性必须人工看联系表（前两格是参考图与场景基准）。",
    }
    review_path = folder / "review.json"
    ensure_parent(review_path)
    review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    return review
