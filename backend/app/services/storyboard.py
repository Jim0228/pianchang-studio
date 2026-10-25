from __future__ import annotations

import io
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import Asset, Project, Script, Shot
from ..utils import ensure_parent, get_font, json_dumps, new_id, sha256_file
from .providers.rest_art import RestArtClient

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


def _merge_negative(skill_negative: str) -> str:
    """商品一致性约束不依赖 Skill：人物、手、文字、多余商品一律禁止。"""
    hard = [
        "no people",
        "no hands",
        "no text",
        "no letters",
        "no price tag",
        "no watermark",
        "no extra products",
        "no second product",
        "no duplicate product",
    ]
    merged = [item.strip() for item in (skill_negative or "").split(",") if item.strip()]
    lowered = {item.lower() for item in merged}
    for item in hard:
        if item.lower() not in lowered:
            merged.append(item)
    return ", ".join(merged)


def build_storyboard_prompt(
    shot: Shot, brief: dict, skill_config: dict, ratio: str = "9:16", description: str = ""
) -> str:
    """镜头级分镜图 prompt：身份锁定 + 风格锚点 + 画面 + 反负向词。"""
    style = skill_config.get("prompt") or {}
    negative = _merge_negative(str(style.get("negative") or ""))
    direction = (shot.prompt or "").strip()
    scene = (shot.scene or shot.action or "").strip()
    camera = (shot.camera or "static mid-shot").strip()
    product = _product_visual_text(brief, description)
    lines = [
        IDENTITY_LOCK,
        f"Product: {product}.",
        "Exactly one unit of this product is in frame. Match the reference image: same shape, "
        "same color variant, same screen, same brush head, same logo and proportions.",
        str(style.get("style") or "Photorealistic e-commerce commercial photography").strip(),
        f"Scene: {scene}.",
        f"Camera: {camera}.",
        f"Lighting: {str(style.get('lighting') or 'soft natural window light').strip()}.",
    ]
    palette = style.get("palette") or []
    if palette:
        lines.append("Color palette accents: " + ", ".join(str(item) for item in palette) + ".")
    lines.append(
        f"Framing: {ratio} vertical, the product occupies 40-60% of the frame. "
        "The frame contains only the product and simple scene props — nothing else."
    )
    lines.append("The frame must be text-free: no text, no letters, no numbers, no captions.")
    if direction:
        lines.append(f"Direction: {direction}")
    lines.append(f"Avoid: {negative}.")
    return "\n".join(lines)


def build_video_prompt(shot: Shot, skill_config: dict) -> str:
    """图生视频 prompt：首帧固定，只允许轻微运镜与光线变化。"""
    style = skill_config.get("prompt") or {}
    camera = (shot.camera or "slow push-in").strip()
    return (
        "Use the first provided image as the exact first frame and keep its composition. "
        f"Animate only a subtle {camera} and a gentle natural light shift over the clip. "
        "Keep the product's shape, proportions, color, material, label, logo and screen unchanged; "
        "no morphing, no extra objects, no people, no text, no watermark. "
        f"{str(style.get('style') or '').strip()} "
        "Photorealistic e-commerce commercial quality."
    ).strip()


def _originals(session: Session, project_id: str, limit: int = 2) -> list[Path]:
    rows = (
        session.query(Asset)
        .filter(Asset.project_id == project_id, Asset.kind == "original")
        .order_by(Asset.created_at.asc())
        .limit(limit)
        .all()
    )
    return [Path(row.path) for row in rows]


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
    if existing is not None and not force and (settings.data_path / existing.path).exists():
        shot.keyframe_asset_id = existing.id
        shot.status = "storyboard_ready"
        session.commit()
        return existing

    reference_paths = [settings.data_path / path for path in _originals(session, project.id)]
    reference_paths = [path for path in reference_paths if path.exists()]
    prompt = build_storyboard_prompt(
        shot, brief, skill_config, script.ratio, project.description or ""
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

    asset = Asset(
        id=new_id("ast"),
        project_id=project.id,
        kind="storyboard",
        filename=path.name,
        path=path.relative_to(settings.data_path).as_posix(),
        width=settings.video_width,
        height=settings.video_height,
        sha256=sha256_file(path),
        meta_json=json_dumps(
            {
                "shot_idx": shot.idx,
                "mode": "rest_image",
                "job_id": str(job.get("job_id") or ""),
                "model": job.get("model"),
                "prompt": prompt,
                "baked_text": False,
                "review": "pending",
            }
        ),
    )
    session.add(asset)
    session.flush()
    shot.keyframe_asset_id = asset.id
    shot.status = "storyboard_ready"
    shot.error = ""
    session.commit()
    return asset


def _contact_sheet(paths: list[Path], reference: Path | None, title: str, out_path: Path) -> None:
    thumb_w, thumb_h = 360, 640
    margin, label_h = 24, 54
    items = ([reference] if reference else []) + paths
    cols = 3
    rows = max(1, (len(items) + cols - 1) // cols)
    width = cols * thumb_w + (cols + 1) * margin
    height = 120 + rows * (thumb_h + label_h) + (rows + 1) * margin
    canvas = Image.new("RGB", (width, height), "#1d1712")
    draw = ImageDraw.Draw(canvas)
    draw.text((margin, 34), f"STORYBOARD · {title}", font=get_font(30, bold=True), fill=(255, 255, 255))
    draw.text(
        (margin, 76),
        "REFERENCE first, then SHOT 01..N — 逐镜人工核对商品一致性",
        font=get_font(14),
        fill=(190, 178, 160),
    )
    for index, path in enumerate(items):
        row, col = divmod(index, cols)
        x = margin + col * (thumb_w + margin)
        y = 120 + margin + row * (thumb_h + label_h + margin)
        image = ImageOps.fit(Image.open(path).convert("RGB"), (thumb_w, thumb_h), method=Image.LANCZOS)
        canvas.paste(image, (x, y))
        draw.rectangle([x, y + thumb_h, x + thumb_w, y + thumb_h + label_h], fill="#2b211b")
        label = "REFERENCE" if index == 0 and reference else f"SHOT {index + 1 - (1 if reference else 0):02d}"
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

    folder = settings.data_path / "projects" / project.id / "storyboards"
    contact = folder / "contact_sheet.jpg"
    if paths:
        _contact_sheet(paths, reference, skill_config.get("name", script.skill_id), contact)

    review = {
        "script_id": script.id,
        "reference": reference.relative_to(settings.data_path).as_posix() if reference else None,
        "contact_sheet": contact.relative_to(settings.data_path).as_posix() if paths else None,
        "normalization": f"{settings.video_width}x{settings.video_height} center-crop",
        "shots": rows,
        "checklist": REVIEW_CHECKLIST,
        "automated_note": "自动检查只验证尺寸与文件存在；商品一致性必须人工看联系表（第一格是参考图）。",
    }
    review_path = folder / "review.json"
    ensure_parent(review_path)
    review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    return review
