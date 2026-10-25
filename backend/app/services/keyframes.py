from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import Asset, Project, Script, Shot
from ..utils import ensure_parent, get_font, json_dumps, new_id, sha256_file, truncate, wrap_text


def _hex(value: str, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
    value = (value or "").strip().lstrip("#")
    if len(value) != 6:
        return fallback
    try:
        return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return fallback


def _gradient(width: int, height: int, palette: list[str]) -> Image.Image:
    top = _hex(palette[0] if len(palette) > 0 else "#F6F1E7", (246, 241, 231))
    bottom = _hex(palette[1] if len(palette) > 1 else "#E8D9C5", (232, 217, 197))
    image = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(image)
    for y in range(height):
        ratio = y / max(1, height - 1)
        color = tuple(int(top[i] * (1 - ratio) + bottom[i] * ratio) for i in range(3))
        draw.line([(0, y), (width, y)], fill=color)
    return image


def _decorate(image: Image.Image, palette: list[str]) -> Image.Image:
    width, height = image.size
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    accent = _hex(palette[2] if len(palette) > 2 else "#2E2A26", (46, 42, 38))
    draw.ellipse([width - 330, -180, width + 280, 430], fill=(*accent, 26))
    draw.ellipse([-220, height - 520, 360, height + 60], fill=(255, 255, 255, 34))
    for offset in range(0, 5):
        y = 260 + offset * 16
        draw.line([(64, y), (260, y)], fill=(*accent, 40), width=3)
    return Image.alpha_composite(image.convert("RGBA"), overlay)


def _product_image(settings: Settings, project_id: str) -> Image.Image | None:
    from ..db import SessionLocal

    session = SessionLocal()
    try:
        asset = (
            session.query(Asset)
            .filter(Asset.project_id == project_id, Asset.kind == "original")
            .order_by(Asset.created_at.asc())
            .first()
        )
        if asset is None:
            return None
        path = settings.data_path / asset.path
        if not path.exists():
            return None
        image = Image.open(path)
        image = image.convert("RGBA")
        image.thumbnail((760, 980))
        return image
    finally:
        session.close()


def _center_text(draw: ImageDraw.ImageDraw, text: str, font, y: int, fill, width: int, max_width: int) -> int:
    lines = wrap_text(text, font, max_width, draw)
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        line_height = bbox[3] - bbox[1]
        draw.text(((width - (bbox[2] - bbox[0])) / 2, y), line, font=font, fill=fill)
        y += line_height + 12
    return y


def _ai_label(draw: ImageDraw.ImageDraw, width: int, height: int, text: str = "AI 生成") -> None:
    font = get_font(22)
    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0] + 20
    h = bbox[3] - bbox[1] + 12
    x = width - w - 40
    y = height - h - 40
    draw.rounded_rectangle([x, y, x + w, y + h], radius=8, fill=(0, 0, 0, 110))
    draw.text((x + 10, y + 4), text, font=font, fill=(255, 255, 255, 210))


def generate_keyframe(
    session: Session,
    settings: Settings,
    project: Project,
    script: Script,
    shot: Shot,
    skill_config: dict,
    brief: dict,
    total: int,
) -> Asset:
    width, height = settings.video_width, settings.video_height
    palette = (skill_config.get("prompt") or {}).get("palette") or []
    image = _gradient(width, height, palette)
    image = _decorate(image, palette)

    product = _product_image(settings, project.id)
    if product is not None:
        shadow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow)
        center_x = width // 2
        center_y = 950
        shadow_draw.ellipse(
            [center_x - 320, center_y + 360, center_x + 320, center_y + 520],
            fill=(0, 0, 0, 70),
        )
        shadow = shadow.filter(ImageFilter.GaussianBlur(28))
        image = Image.alpha_composite(image, shadow)

        x = center_x - product.width // 2
        y = center_y - product.height // 2
        image.alpha_composite(product, (x, y))

    draw = ImageDraw.Draw(image)

    # 顶部品牌/镜号
    badge_font = get_font(24, bold=True)
    badge_text = f"SHOT {shot.idx:02d}/{total:02d} · {skill_config.get('name', 'VIDEO')}"
    bbox = draw.textbbox((0, 0), badge_text, font=badge_font)
    badge_w = bbox[2] - bbox[0] + 36
    badge_h = bbox[3] - bbox[1] + 20
    draw.rounded_rectangle([48, 56, 48 + badge_w, 56 + badge_h], radius=badge_h // 2, fill=(20, 18, 16, 150))
    draw.text((48 + 18, 56 + 8), badge_text, font=badge_font, fill=(255, 255, 255, 230))

    # 标题
    title_font = get_font(58, bold=True)
    title_hex = palette[2] if len(palette) > 2 else "#2E2A26"
    title_color = _hex(title_hex, (46, 42, 38))
    _center_text(
        draw,
        truncate(brief.get("title", shot.scene), 22),
        title_font,
        150,
        (*title_color, 255),
        width,
        880,
    )

    # 底部字幕条
    if shot.on_screen:
        font = get_font(52, bold=True)
        lines = wrap_text(shot.on_screen, font, 800, draw)
        text_height = sum(draw.textbbox((0, 0), line, font=font)[3] for line in lines) + 20 * len(lines)
        box_h = text_height + 56
        box_y = height - int(height * 0.16) - box_h
        draw.rounded_rectangle(
            [90, box_y, width - 90, box_y + box_h],
            radius=22,
            fill=(18, 16, 14, 176),
        )
        y = box_y + 26
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            draw.text(((width - (bbox[2] - bbox[0])) / 2, y), line, font=font, fill=(255, 224, 138, 255))
            y += bbox[3] - bbox[1] + 18

    _ai_label(draw, width, height)

    folder = settings.data_path / "projects" / project.id / "keyframes"
    path = folder / f"shot_{shot.idx:02d}.jpg"
    ensure_parent(path)
    image.convert("RGB").save(path, "JPEG", quality=92)

    asset = Asset(
        id=new_id("ast"),
        project_id=project.id,
        kind="keyframe",
        filename=path.name,
        path=path.relative_to(settings.data_path).as_posix(),
        width=width,
        height=height,
        sha256=sha256_file(path),
        meta_json=json_dumps(
            {
                "shot_idx": shot.idx,
                "prompt": shot.prompt,
                "skill_id": skill_config.get("id"),
                "ai_label": True,
            }
        ),
    )
    session.add(asset)
    shot.keyframe_asset_id = asset.id
    shot.status = "keyframe_ready"
    session.commit()
    return asset


def generate_plate(
    session: Session,
    settings: Settings,
    project: Project,
    shot: Shot,
    skill_config: dict,
) -> Asset:
    """A clean, text-free scene plate used as the first frame for MCP video."""
    width, height = settings.video_width, settings.video_height
    palette = (skill_config.get("prompt") or {}).get("palette") or []
    image = _gradient(width, height, palette)
    image = _decorate(image, palette)

    product = _product_image(settings, project.id)
    if product is not None:
        shadow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow)
        center_x = width // 2
        center_y = 950
        shadow_draw.ellipse(
            [center_x - 320, center_y + 360, center_x + 320, center_y + 520],
            fill=(0, 0, 0, 70),
        )
        shadow = shadow.filter(ImageFilter.GaussianBlur(28))
        image = Image.alpha_composite(image, shadow)
        image.alpha_composite(product, (center_x - product.width // 2, center_y - product.height // 2))

    folder = settings.data_path / "projects" / project.id / "plates"
    path = folder / f"shot_{shot.idx:02d}.jpg"
    ensure_parent(path)
    image.convert("RGB").save(path, "JPEG", quality=94)

    asset = Asset(
        id=new_id("ast"),
        project_id=project.id,
        kind="plate",
        filename=path.name,
        path=path.relative_to(settings.data_path).as_posix(),
        width=width,
        height=height,
        sha256=sha256_file(path),
        meta_json=json_dumps({"shot_idx": shot.idx, "clean": True, "for": "mcp_video"}),
    )
    session.add(asset)
    session.commit()
    return asset


def ensure_plate(
    session: Session,
    settings: Settings,
    project: Project,
    shot: Shot,
    skill_config: dict,
) -> Asset:
    filename = f"shot_{shot.idx:02d}.jpg"
    existing = (
        session.query(Asset)
        .filter(Asset.project_id == project.id, Asset.kind == "plate", Asset.filename == filename)
        .first()
    )
    if existing is not None:
        return existing
    return generate_plate(session, settings, project, shot, skill_config)


def ensure_ai_label(settings: Settings, width: int, height: int) -> Path:
    folder = settings.data_path / "assets"
    path = folder / f"ai_label_{width}x{height}.png"
    if path.exists():
        return path
    ensure_parent(path)
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    _ai_label(draw, width, height)
    image.save(path, "PNG")
    return path


def generate_subtitle_png(
    session: Session,
    settings: Settings,
    project: Project,
    shot: Shot,
) -> Asset | None:
    text = (shot.on_screen or "").strip()
    if not text:
        return None
    width, height = settings.video_width, settings.video_height
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    font = get_font(max(32, int(width * 0.048)), bold=True)
    lines = wrap_text(text, font, int(width * 0.78), draw)
    line_height = max(draw.textbbox((0, 0), line, font=font)[3] for line in lines) if lines else 46
    pad = 28
    box_w = int(width * 0.84)
    box_h = line_height * len(lines) + pad * 2 + 10 * max(0, len(lines) - 1)
    x = (width - box_w) // 2
    y = height - int(height * 0.17) - box_h
    draw.rounded_rectangle([x, y, x + box_w, y + box_h], radius=18, fill=(16, 14, 12, 184))
    draw.rounded_rectangle([x, y, x + 8, y + box_h], radius=4, fill=(255, 190, 64, 255))
    current_y = y + pad
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        draw.text(
            (x + (box_w - (bbox[2] - bbox[0])) / 2, current_y),
            line,
            font=font,
            fill=(255, 255, 255, 255),
        )
        current_y += line_height + 10

    folder = settings.data_path / "projects" / project.id / "subtitles"
    path = folder / f"subtitle_{shot.idx:02d}.png"
    ensure_parent(path)
    image.save(path, "PNG")

    asset = Asset(
        id=new_id("ast"),
        project_id=project.id,
        kind="subtitle",
        filename=path.name,
        path=path.relative_to(settings.data_path).as_posix(),
        width=width,
        height=height,
        sha256=sha256_file(path),
        meta_json=json_dumps({"shot_idx": shot.idx, "text": text}),
    )
    session.add(asset)
    shot.subtitle_asset_id = asset.id
    session.commit()
    return asset


def generate_cover(
    session: Session,
    settings: Settings,
    project: Project,
    script: Script,
    keyframe_asset: Asset | None,
    brief: dict,
) -> Asset:
    width, height = settings.video_width, settings.video_height
    folder = settings.data_path / "projects" / project.id / "covers"
    path = folder / "cover.jpg"
    ensure_parent(path)

    if keyframe_asset is not None and (settings.data_path / keyframe_asset.path).exists():
        image = Image.open(settings.data_path / keyframe_asset.path).convert("RGBA")
    else:
        image = _gradient(width, height, ["#F6F1E7", "#E8D9C5"]).convert("RGBA")

    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rectangle([0, int(height * 0.55), width, height], fill=(0, 0, 0, 120))
    image = Image.alpha_composite(image, overlay)

    data = json.loads(script.script_json)
    cover = data.get("cover") or {}
    title = cover.get("title") or (brief.get("selling_points") or ["好物"])[0]
    subtitle = cover.get("subtitle") or brief.get("title", "")

    draw = ImageDraw.Draw(image)
    title_font = get_font(96, bold=True)
    _center_text(draw, truncate(title, 10), title_font, int(height * 0.66), (255, 255, 255, 255), width, 900)
    subtitle_font = get_font(46)
    _center_text(draw, truncate(subtitle, 20), subtitle_font, int(height * 0.80), (255, 228, 170, 255), width, 860)
    _ai_label(draw, width, height)

    image.convert("RGB").save(path, "JPEG", quality=94)
    asset = Asset(
        id=new_id("ast"),
        project_id=project.id,
        kind="cover",
        filename=path.name,
        path=path.relative_to(settings.data_path).as_posix(),
        width=width,
        height=height,
        sha256=sha256_file(path),
        meta_json=json_dumps({"ai_label": True}),
    )
    session.add(asset)
    session.commit()
    return asset
