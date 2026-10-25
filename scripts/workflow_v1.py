from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageOps, ImageStat

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.config import Settings
from backend.app.services.ffmpeg import normalize_video, probe
from backend.app.services.keyframes import ensure_ai_label
from backend.app.services.providers.deepseek import DeepSeekClient
from backend.app.services.providers.rest_art import RestArtClient
from backend.app.utils import get_font, wrap_text

WORK = ROOT / "data" / "workflow_v1"
INPUT = WORK / "01_input"
STYLE_DIR = WORK / "02_style"
SCRIPT_DIR = WORK / "03_script"
STORY_DIR = WORK / "04_storyboard"
VIDEO_DIR = WORK / "05_video"

RAW_DESCRIPTION = (
    "厨房油污清洁剂 500ml，植物配方，不伤手，一喷一擦，"
    "厨房灶台、油烟机都能用，39.9 元。"
)

MANUAL_VISUAL = {
    "shape": "cylindrical spray bottle",
    "body_color": "matte dark brown",
    "label": "cream rectangular label panel on the front",
    "mark": "small orange circular mark on the label",
    "trigger": "dark spray trigger",
    "material": "frosted plastic",
    "capacity": "500ml",
    "proportions": "tall slim bottle, label occupies the middle third",
}

IDENTITY_LOCK = (
    "PRODUCT IDENTITY LOCK (must be identical in every image): a 500ml cylindrical spray bottle "
    "with a matte dark-brown body, a cream rectangular label panel on the front, a small orange "
    "circular mark on the label, and a dark spray trigger. Match the reference image exactly. "
    "Do not restyle, recolor, resize proportions, move the label, or add/remove parts."
)

STYLE = {
    "id": "warm_premium_lifestyle",
    "name": "暖调高级生活感",
    "platform": "douyin",
    "tone": "高级、温暖、可信，不是硬广",
    "palette": ["#F6F1E7", "#D8C4A7", "#2E2A26", "#FF6B3D"],
    "lighting": "soft window side light 5200K, gentle shadow, warm highlights, clean cream background",
    "camera": "slow push-in, macro detail, static mid-shot, shallow depth of field",
    "composition": "product centered or rule of thirds, clean uncluttered counter, no competing objects",
    "transition": "cut / match cut",
    "negative": "no text, no watermark, no extra products, no people faces, no deformed label, no duplicate bottle",
    "prompt_prefix": (
        "Photorealistic e-commerce commercial photography, warm premium lifestyle, "
        "soft window side light 5200K, cream and beige palette with a subtle orange accent, "
        "shallow depth of field, clean composition, high detail."
    ),
}

REQUIRED_SHOTS = 5
TOTAL_SECONDS = 15


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def reference_image() -> Path:
    copied = INPUT / "reference.jpg"
    if copied.exists():
        return copied
    project = ROOT / "data" / "projects" / "prj_3f03d4e5fbac" / "originals"
    candidates = sorted(
        path for path in project.glob("*.jpg") if not path.name.endswith("_thumb.jpg")
    )
    if not candidates:
        raise SystemExit("找不到原始产品图")
    INPUT.mkdir(parents=True, exist_ok=True)
    shutil.copy2(candidates[0], copied)
    return copied


async def stage_input(settings: Settings) -> dict:
    reference = reference_image()
    client = DeepSeekClient(settings)
    system = (
        "你是电商短视频前期策划。把商品描述结构化为严格 JSON，不要 Markdown。"
        "必须区分 product_visual（只描述画面里能看到的主体，不得虚构）、"
        "scene_elements（背景/环境/道具）、selling_points（留给脚本）、"
        "visual_elements（只负责视觉）。字段：subject, product_visual, scene_elements, "
        "selling_points, visual_elements, forbidden, source_type, missing_inputs, notes。"
    )
    user = (
        f"商品描述：{RAW_DESCRIPTION}\n"
        f"参考图人工标注（必须原样采用）：{json.dumps(MANUAL_VISUAL, ensure_ascii=False)}\n"
        "这是一张用户实拍/单角度产品图，请说明缺少的多角度素材。"
    )
    data = await client._chat_json(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.2,
        max_tokens=1800,
    )
    brief = {
        "raw_description": RAW_DESCRIPTION,
        "subject": data.get("subject") or "厨房油污清洁剂 500ml",
        "product_visual": MANUAL_VISUAL,
        "scene_elements": data.get("scene_elements")
        or ["kitchen counter", "oil stain", "spray mist", "wiping cloth", "bright kitchen"],
        "selling_points": data.get("selling_points")
        or ["植物配方", "不伤手", "一喷一擦", "厨房灶台/油烟机适用", "39.9 元"],
        "visual_elements": data.get("visual_elements")
        or ["oil stain close-up", "spray mist", "wiped clean surface", "warm window light"],
        "forbidden": [
            "text in image",
            "watermark",
            "extra products",
            "people faces",
            "distorted or changed label",
            "fabricated certifications",
        ],
        "source_type": "user_photo_single_angle",
        "missing_inputs": data.get("missing_inputs")
        or ["multi-angle product photos", "detail shots", "scene reference photos"],
        "notes": (
            "DeepSeek 只做文本结构化；product_visual 由人工从参考图标注。"
            "单角度素材限制了一致性锚点强度。"
        ),
        "reference_image": str(reference.relative_to(ROOT)),
    }
    write_json(INPUT / "brief.json", brief)
    print("[input] saved", INPUT / "brief.json")
    return brief


def stage_style() -> dict:
    write_json(STYLE_DIR / "style.json", STYLE)
    print("[style] saved", STYLE_DIR / "style.json")
    return STYLE


async def stage_script(settings: Settings, brief: dict, style: dict) -> dict:
    client = DeepSeekClient(settings)
    system = (
        "你是资深电商短视频编导。根据商品档案与风格锚点，输出严格 JSON，不要 Markdown。"
        "只打 1-2 个核心卖点；卖点写进 voiceover/on_screen，不要写进画面描述。"
        "画面描述只负责视觉，不得出现文字、价格牌、认证、销量。"
    )
    user = json.dumps(
        {
            "brief": brief,
            "style": style,
            "requirements": {
                "shots": REQUIRED_SHOTS,
                "total_seconds": TOTAL_SECONDS,
                "structure": "hook 0-3s → problem 3-6s → reveal 6-10s → proof 10-13s → cta 13-15s",
                "core_selling_point": "一喷一擦",
                "secondary_selling_point": "植物配方，不伤手",
                "fields": ["idx", "seconds", "shot_size", "camera", "visual", "voiceover", "on_screen", "transition"],
                "image_rule": "每镜画面必须包含同一款产品，但不要出现任何文字；字幕后期叠加",
                "avoid": "多人对话、精确手势、复杂物理、品牌字、车牌",
            },
        },
        ensure_ascii=False,
    )
    data = await client._chat_json(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.7,
        max_tokens=4000,
    )
    shots = data.get("shots")
    if not isinstance(shots, list) or len(shots) != REQUIRED_SHOTS:
        raise SystemExit(f"脚本镜头数异常：{len(shots) if isinstance(shots, list) else 'N/A'}")
    cleaned = []
    for index, shot in enumerate(shots, start=1):
        cleaned.append(
            {
                "idx": index,
                "seconds": float(shot.get("seconds") or 3),
                "shot_size": str(shot.get("shot_size") or "中景"),
                "camera": str(shot.get("camera") or "缓慢推近"),
                "visual": str(shot.get("visual") or ""),
                "voiceover": str(shot.get("voiceover") or ""),
                "on_screen": str(shot.get("on_screen") or ""),
                "transition": str(shot.get("transition") or "cut"),
            }
        )
    total = sum(shot["seconds"] for shot in cleaned)
    cleaned[-1]["seconds"] = max(1.0, cleaned[-1]["seconds"] + (TOTAL_SECONDS - total))
    script = {
        "title": data.get("title") or "厨房油污清洁剂｜暖调高级生活感",
        "style_id": style["id"],
        "core_selling_point": data.get("core_selling_point") or "一喷一擦",
        "hook": data.get("hook") or "灶台油污，擦不掉？",
        "shots": cleaned,
        "caption": data.get("caption")
        or {
            "title": "厨房油污，一喷一擦",
            "body": "植物配方，不伤手；灶台、油烟机都能用。",
            "hashtags": ["#厨房清洁", "#家居好物", "#清洁神器"],
        },
    }
    write_json(SCRIPT_DIR / "script.json", script)
    print("[script] saved", SCRIPT_DIR / "script.json")
    print("[script] shots", [(shot["seconds"], shot["visual"][:30]) for shot in cleaned])
    return script


def color_similarity(a: Image.Image, b: Image.Image) -> float:
    a = ImageOps.fit(a.convert("RGB"), (64, 64))
    b = ImageOps.fit(b.convert("RGB"), (64, 64))
    histogram_a = a.histogram()
    histogram_b = b.histogram()
    intersection = sum(min(x, y) for x, y in zip(histogram_a, histogram_b))
    total = sum(histogram_a)
    return round(intersection / total, 4) if total else 0.0


def make_contact_sheet(paths: list[Path], style: dict, out_path: Path, reference: Path) -> None:
    thumb_w, thumb_h = 360, 640
    cols, rows = 3, 2
    margin = 24
    label_h = 54
    width = cols * thumb_w + (cols + 1) * margin
    height = 120 + rows * (thumb_h + label_h) + (rows + 1) * margin
    canvas = Image.new("RGB", (width, height), "#1d1712")
    draw = ImageDraw.Draw(canvas)
    title_font = get_font(30, bold=True)
    label_font = get_font(18)
    draw.text((margin, 34), f"STORYBOARD · {style['name']}", font=title_font, fill=(255, 255, 255))
    draw.text((margin, 76), "REFERENCE first, then SHOT 01-05", font=get_font(14), fill=(190, 178, 160))
    items = [(reference, "REFERENCE")] + [
        (path, f"SHOT {index + 1:02d}") for index, path in enumerate(paths)
    ]
    for index, (path, label) in enumerate(items):
        row, col = divmod(index, cols)
        x = margin + col * (thumb_w + margin)
        y = 120 + margin + row * (thumb_h + label_h + margin)
        image = Image.open(path).convert("RGB")
        image = ImageOps.fit(image, (thumb_w, thumb_h), method=Image.LANCZOS)
        canvas.paste(image, (x, y))
        draw.rectangle([x, y + thumb_h, x + thumb_w, y + thumb_h + label_h], fill="#2b211b")
        draw.text((x + 12, y + thumb_h + 12), label, font=label_font, fill=(255, 224, 138))
    canvas.save(out_path, "JPEG", quality=92)


async def stage_storyboard(settings: Settings, brief: dict, style: dict, script: dict) -> dict:
    reference = reference_image()
    client = RestArtClient(settings)
    paths: list[Path] = []
    review: list[dict] = []
    ref_image = Image.open(reference)
    STORY_DIR.mkdir(parents=True, exist_ok=True)

    for shot in script["shots"]:
        path = STORY_DIR / f"shot_{shot['idx']:02d}.jpg"
        prompt = (
            f"{IDENTITY_LOCK}\n"
            f"{style['prompt_prefix']}\n"
            f"Shot {shot['idx']}: {shot['shot_size']}, {shot['camera']}. {shot['visual']}\n"
            "The product stays exactly identical to the reference image; only the scene, camera and lighting change. "
            f"{style['negative']}."
        )
        if path.exists():
            print(f"[storyboard] reuse shot {shot['idx']}")
        else:
            print(f"[storyboard] generating shot {shot['idx']} ...", flush=True)
            data, job = await client.generate_image(
                prompt,
                [reference],
                params={"aspectRatio": "portrait", "quality": "high"},
            )
            path.write_bytes(data)
            print(
                f"[storyboard] shot {shot['idx']} saved {len(data)} bytes "
                f"job={job.get('job_id')} status={job.get('status')}",
                flush=True,
            )
        paths.append(path)
        image = Image.open(path).convert("RGB")
        if image.size != (1080, 1920):
            image = ImageOps.fit(image, (1080, 1920), method=Image.LANCZOS)
            image.save(path, "JPEG", quality=94)
        review.append(
            {
                "idx": shot["idx"],
                "file": str(path.relative_to(WORK)),
                "width": image.width,
                "height": image.height,
                "aspect_ratio_normalized": True,
                "color_similarity_to_reference": color_similarity(ref_image, image),
                "prompt": prompt,
                "status": "pending_manual_review",
            }
        )

    contact = STORY_DIR / "contact_sheet.jpg"
    make_contact_sheet(paths, style, contact, reference)
    data = {
        "style_id": style["id"],
        "reference": str(reference.relative_to(WORK)),
        "contact_sheet": str(contact.relative_to(WORK)),
        "normalization": "all storyboards center-cropped to 1080x1920 (9:16)",
        "shots": review,
        "manual_review_checklist": [
            "reference vs each shot: same bottle shape/proportions",
            "label position, cream panel and orange mark unchanged",
            "body color/material unchanged",
            "no text/watermark/extra products",
            "lighting and color temperature match across shots",
            "shot-to-shot framing allows cut/match-cut",
        ],
        "automated_note": (
            "color_similarity_to_reference 只是全图直方图重叠，不能证明商品身份；"
            "商品一致性必须人工看 contact_sheet（第一格为 REFERENCE）。"
        ),
    }
    write_json(STORY_DIR / "review.json", data)
    print("[storyboard] contact sheet", contact)
    return data


def make_subtitle_png(text: str, out_path: Path) -> None:
    width, height = 1080, 1920
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    font = get_font(52, bold=True)
    lines = wrap_text(text, font, 800, draw)
    line_height = max(draw.textbbox((0, 0), line, font=font)[3] for line in lines) if lines else 46
    pad = 28
    box_w = 920
    box_h = line_height * len(lines) + pad * 2 + 10 * max(0, len(lines) - 1)
    x = (width - box_w) // 2
    y = height - int(height * 0.17) - box_h
    draw.rounded_rectangle([x, y, x + box_w, y + box_h], radius=18, fill=(16, 14, 12, 190))
    draw.rounded_rectangle([x, y, x + 8, y + box_h], radius=4, fill=(255, 190, 64, 255))
    current = y + pad
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        draw.text(
            (x + (box_w - (bbox[2] - bbox[0])) / 2, current),
            line,
            font=font,
            fill=(255, 255, 255, 255),
        )
        current += line_height + 10
    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path, "PNG")


async def stage_video(settings: Settings, script: dict, review: dict) -> dict:
    shot_idx = 3
    shot = next((item for item in script["shots"] if item["idx"] == shot_idx), script["shots"][2])
    storyboard = STORY_DIR / f"shot_{shot['idx']:02d}.jpg"
    reference = reference_image()
    raw_path = VIDEO_DIR / f"shot_{shot['idx']:02d}_raw.mp4"
    final_path = VIDEO_DIR / f"shot_{shot['idx']:02d}.mp4"
    subtitle = VIDEO_DIR / f"shot_{shot['idx']:02d}_subtitle.png"
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)

    if not raw_path.exists():
        prompt = (
            "Use the first provided image as the exact first frame. Keep the product and composition "
            "identical. Animate only a very subtle slow push-in and a gentle natural light shift. "
            "Do not deform the bottle, change the label, move the orange mark, add objects, or add text. "
            "Photorealistic e-commerce commercial, 3 seconds."
        )
        client = RestArtClient(settings)
        print("[video] generating shot", shot["idx"], "...", flush=True)
        info = await client.generate_video(
            prompt,
            [storyboard, reference],
            raw_path,
            seconds=3,
            ratio="9:16",
        )
        print("[video] raw saved", raw_path, info, flush=True)
    else:
        print("[video] reuse raw", raw_path)

    if not final_path.exists():
        make_subtitle_png(shot.get("on_screen") or "", subtitle)
        label = ensure_ai_label(settings, 1080, 1920)
        print("[video] post-processing subtitle + AI label + 1080x1920/30fps ...", flush=True)
        normalize_video(
            settings,
            raw_path,
            final_path,
            seconds=3.0,
            width=1080,
            height=1920,
            fps=30,
            subtitle=subtitle,
            label=label,
        )
    info = probe(settings, final_path)
    result = {
        "shot_idx": shot["idx"],
        "storyboard": str(storyboard.relative_to(WORK)),
        "raw_video": str(raw_path.relative_to(WORK)),
        "video": str(final_path.relative_to(WORK)),
        "subtitle": str(subtitle.relative_to(WORK)),
        "probe": info,
        "review": "pending_manual_review",
        "multi_version": False,
        "note": "首版只生成 1 个版本；多版本抽帧与首尾帧控制待下一轮。",
    }
    write_json(VIDEO_DIR / "video.json", result)
    print("[video] probe", info)
    return result


def stage_report(brief: dict, style: dict, script: dict, review: dict, video: dict | None) -> None:
    lines = [
        "# 五阶段视频生成工作流 · 跑通记录",
        "",
        "## 1. 输入",
        f"- 原始描述：{brief['raw_description']}",
        f"- 素材类型：{brief['source_type']}",
        f"- 缺少素材：{', '.join(brief['missing_inputs'])}",
        f"- 产品视觉锚点：{json.dumps(brief['product_visual'], ensure_ascii=False)}",
        "",
        "## 2. 风格",
        f"- {style['name']} / {style['platform']}",
        f"- 调性：{style['tone']}",
        f"- 光/色：{style['lighting']}",
        f"- 固定前缀：{style['prompt_prefix']}",
        "",
        "## 3. 脚本",
        f"- 标题：{script['title']}",
        f"- 核心卖点：{script['core_selling_point']}",
        f"- 镜头数：{len(script['shots'])} / 总时长：{sum(s['seconds'] for s in script['shots'])}s",
        "",
        "| # | 秒 | 景别 | 运镜 | 画面 | 口播 | 字幕 |",
        "|---|---|---|---|---|---|---|",
    ]
    for shot in script["shots"]:
        lines.append(
            f"| {shot['idx']} | {shot['seconds']} | {shot['shot_size']} | {shot['camera']} | "
            f"{shot['visual']} | {shot['voiceover']} | {shot['on_screen']} |"
        )
    lines += [
        "",
        "## 4. 分镜图",
        f"- 联系表：{review['contact_sheet']}",
        f"- 自动颜色相似度：{[shot['color_similarity_to_reference'] for shot in review['shots']]}",
        "- 注意：自动相似度只做弱参考，商品身份必须人工看联系表。",
        "- 人工审核清单：",
    ]
    lines += [f"  - [ ] {item}" for item in review["manual_review_checklist"]]
    lines += ["", "## 5. 视频"]
    if video:
        lines += [
            f"- 镜头 {video['shot_idx']}：{video['video']}",
            f"- 探针：{json.dumps(video['probe'], ensure_ascii=False)}",
            "- 只生成 1 个版本；多版本抽帧与首尾帧控制待补。",
        ]
    else:
        lines.append("- 尚未执行。")
    lines += [
        "",
        "## 结论与代码改造点",
        "1. 商品视觉锚点必须单独结构化，不能只靠 DeepSeek 文本；当前是人工标注。",
        "2. 分镜图阶段应成为一等公民：统一风格前缀、商品锁定、参考图、逐镜审核。",
        "3. 需要视觉审核能力（VLM）或人工审核门禁；颜色直方图不能替代商品一致性判断。",
        "4. 视频阶段应从审核通过的分镜图出发，限制运动幅度，并支持多版本抽帧。",
        "5. 输入阶段应校验多角度/细节/场景素材，缺失时明确提示。",
    ]
    (WORK / "workflow_report.md").write_text("\n".join(lines), encoding="utf-8")
    print("[report] saved", WORK / "workflow_report.md")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "stage",
        choices=["all", "input", "style", "script", "storyboard", "video", "report"],
        nargs="?",
        default="all",
    )
    args = parser.parse_args()
    settings = Settings()
    WORK.mkdir(parents=True, exist_ok=True)

    if args.stage in {"all", "input"} or not (INPUT / "brief.json").exists():
        brief = await stage_input(settings)
    else:
        brief = read_json(INPUT / "brief.json")
    if args.stage in {"all", "style"} or not (STYLE_DIR / "style.json").exists():
        style = stage_style()
    else:
        style = read_json(STYLE_DIR / "style.json")
    if args.stage in {"all", "script"} or not (SCRIPT_DIR / "script.json").exists():
        script = await stage_script(settings, brief, style)
    else:
        script = read_json(SCRIPT_DIR / "script.json")
    review = read_json(STORY_DIR / "review.json") if (STORY_DIR / "review.json").exists() else None
    video = read_json(VIDEO_DIR / "video.json") if (VIDEO_DIR / "video.json").exists() else None

    if args.stage in {"all", "storyboard"}:
        review = await stage_storyboard(settings, brief, style, script)
    if args.stage in {"all", "video"}:
        if review is None:
            raise SystemExit("请先生成分镜图")
        video = await stage_video(settings, script, review)
    if args.stage in {"all", "report"}:
        if review is None:
            raise SystemExit("请先生成分镜图")
        stage_report(brief, style, script, review, video)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
