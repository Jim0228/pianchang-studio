from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..config import Settings
from ..errors import AppError
from ..models import Asset, Product, Project, Script, Shot, Skill
from ..utils import json_dumps, json_loads, new_id, truncate
from .compliance import check_script
from .platforms import get_platform

BEAT_VO: dict[str, list[str]] = {
    "hook": ["{pain}，你是不是也遇到过？"],
    "problem": ["每次{scene_short}，都特别费劲"],
    "reveal": ["直到我遇到{title}"],
    "proof": ["{sp1}，{sp2}，实测看得见"],
    "cta": ["现在下单，{price}元到手"],
    "unbox": ["今天开箱{title}"],
    "detail": ["细节看一下：{scene_short}"],
    "test": ["实测：{sp1}"],
    "verdict": ["总结：{sp1}"],
    "texture": ["看这个质地，{sp1}"],
    "usage": ["用起来很简单：{scene_short}"],
    "scene1": ["{scene_short}"],
    "scene2": ["它很自然地融入了生活"],
    "product": ["{title}，{sp1}"],
    "origin": ["源头在这里：{scene_short}"],
    "craft": ["工艺上，{sp1}"],
    "quality": ["每一批都经过检查"],
    "step1": ["第一步：{scene_short}"],
    "step2": ["第二步：{sp1}"],
    "step3": ["第三步：{sp2}"],
    "result": ["效果看得见"],
}

BEAT_SCREEN: dict[str, list[str]] = {
    "hook": ["{pain}？", "别再踩坑"],
    "problem": ["太真实了", "每次都费劲"],
    "reveal": ["{sp1}", "{title}"],
    "proof": ["实测有效", "{sp1}"],
    "cta": ["点击下方", "现在下单"],
    "unbox": ["开箱", "第一印象"],
    "detail": ["看细节", "{sp1}"],
    "test": ["实测", "真实使用"],
    "verdict": ["值得入", "{sp2}"],
    "texture": ["看质感", "{sp1}"],
    "usage": ["这样用", "很简单"],
    "scene1": ["生活场景", "慢下来"],
    "scene2": ["自然融入", "刚刚好"],
    "product": ["{sp1}", "{title}"],
    "origin": ["源头", "产地直击"],
    "craft": ["工艺", "{sp1}"],
    "quality": ["质检", "看得见"],
    "step1": ["第一步", "先这样做"],
    "step2": ["第二步", "{sp1}"],
    "step3": ["第三步", "{sp2}"],
    "result": ["看效果", "完成"],
}

DEFAULT_BEATS = [
    {"id": "hook", "seconds": 3},
    {"id": "problem", "seconds": 3},
    {"id": "reveal", "seconds": 4},
    {"id": "proof", "seconds": 3},
    {"id": "cta", "seconds": 2},
]


def _pick(options: list[str], index: int) -> str:
    if not options:
        return ""
    return options[index % len(options)]


def _fmt(template: str, ctx: dict) -> str:
    try:
        return template.format(**ctx)
    except (KeyError, IndexError, ValueError):
        return template


def _scene_short(scene: str) -> str:
    cleaned = scene.strip("，。, .")
    return truncate(cleaned, 12)


def _trim_beats(beats: list[dict], max_shots: int) -> list[dict]:
    if len(beats) <= max_shots:
        return beats
    if max_shots <= 2:
        return beats[:max_shots]
    return beats[:2] + beats[-(max_shots - 2) :]


def _distribute_seconds(beats: list[dict], duration: int) -> list[int]:
    weights = [max(1, int(beat.get("seconds", 3))) for beat in beats]
    total = sum(weights) or len(weights)
    seconds = [max(1, int(round(duration * weight / total))) for weight in weights]
    while sum(seconds) < duration:
        seconds[-1] += 1
    while sum(seconds) > duration:
        index = max(range(len(seconds)), key=lambda i: seconds[i])
        if seconds[index] <= 1:
            break
        seconds[index] -= 1
    seconds[-1] += duration - sum(seconds)
    return seconds


def _caption(brief: dict, platform: str, skill_name: str) -> dict:
    preset = get_platform(platform)
    title_limit = int(preset.get("title_limit", 30))
    hashtag_count = int(preset.get("hashtag_count", 3))
    points = brief.get("selling_points") or ["品质可靠"]
    title = truncate(f"{brief.get('title', '新品')} {points[0]}", title_limit)
    body = (
        f"{points[0]}，{points[1] if len(points) > 1 else '使用方便'}。"
        f"{brief.get('audience', '')}适用。"
    )
    candidates = [brief.get("category", ""), *points, *brief.get("keywords", [])]
    hashtags: list[str] = []
    for item in candidates:
        tag = "#" + str(item).replace(" ", "").strip("#")
        if len(tag) > 2 and tag not in hashtags:
            hashtags.append(tag)
    return {
        "title": title,
        "body": truncate(body, int(preset.get("body_limit", 1000))),
        "hashtags": hashtags[:hashtag_count],
        "platform_note": preset.get("note", ""),
        "skill_name": skill_name,
    }


def build_mock_script(
    project: Project,
    product: Product,
    skill: Skill,
    platform: str,
    duration: int,
    ratio: str,
) -> dict:
    brief = json_loads(product.brief_json, {})
    config = json_loads(skill.config_json, {})
    beats = list(config.get("beats") or DEFAULT_BEATS)
    shot_cfg = config.get("shots") or {}
    max_shots = int(shot_cfg.get("max", 6))
    min_shots = int(shot_cfg.get("min", 4))
    beats = _trim_beats(beats, max(max_shots, 1))
    if len(beats) < min_shots and beats:
        while len(beats) < min_shots:
            beats.append(dict(beats[-1]))
    seconds = _distribute_seconds(beats, duration)

    scenes = list(config.get("scenes") or ["产品使用场景"])
    cameras = list((config.get("prompt") or {}).get("camera") or ["中景固定"])
    prompt_cfg = config.get("prompt") or {}
    copy_map = config.get("copy") or {}

    points = brief.get("selling_points") or ["品质可靠", "使用方便", "性价比高"]
    ctx = {
        "title": brief.get("title", "新品"),
        "category": brief.get("category", "好物"),
        "pain": brief.get("pain_point", "选来选去总不满意"),
        "audience": brief.get("audience", "有需要的人"),
        "price": brief.get("price") or "限时",
        "sp1": points[0],
        "sp2": points[1] if len(points) > 1 else "使用方便",
        "sp3": points[2] if len(points) > 2 else "品质可靠",
    }

    shots: list[dict] = []
    for index, (beat, shot_seconds) in enumerate(zip(beats, seconds), start=1):
        beat_id = beat.get("id", "hook")
        scene = scenes[(index - 1) % len(scenes)]
        camera = cameras[(index - 1) % len(cameras)]
        scene_short = _scene_short(scene)
        local_ctx = {**ctx, "scene": scene, "scene_short": scene_short, "idx": index}
        vo_options = copy_map.get(beat_id) or BEAT_VO.get(beat_id) or ["{title}"]
        screen_options = BEAT_SCREEN.get(beat_id) or ["{sp1}"]
        voiceover = _fmt(_pick(vo_options, index - 1), local_ctx)
        on_screen = truncate(_fmt(_pick(screen_options, index - 1), local_ctx), 18)
        prompt = "。".join(
            part
            for part in [
                prompt_cfg.get("style", "真实生活质感，电商广告级清晰度"),
                f"画面：{scene}",
                f"镜头：{camera}",
                f"光线：{prompt_cfg.get('lighting', '自然光')}",
                prompt_cfg.get("product_lock", "严格保持商品外形、比例、颜色、材质、Logo 不变"),
                f"画面比例 {ratio}，电商广告级清晰度",
                f"负面：{prompt_cfg.get('negative', '变形, 错误文字, 水印, 低清晰度')}",
            ]
            if part
        )
        shots.append(
            {
                "idx": index,
                "seconds": shot_seconds,
                "beat": beat_id,
                "scene": scene,
                "camera": camera,
                "action": f"{camera}，突出{scene_short}",
                "voiceover": voiceover,
                "on_screen": on_screen,
                "prompt": prompt,
            }
        )

    data: dict[str, Any] = {
        "title": truncate(f"{brief.get('title', '新品')}｜{skill.name}", 40),
        "skill_id": skill.id,
        "skill_version": skill.version,
        "platform": platform,
        "ratio": ratio,
        "duration": duration,
        "style_bible": {
            "style": prompt_cfg.get("style", ""),
            "lighting": prompt_cfg.get("lighting", ""),
            "palette": prompt_cfg.get("palette", []),
            "camera": cameras,
            "negative": prompt_cfg.get("negative", ""),
            "product_lock": prompt_cfg.get("product_lock", ""),
        },
        "shots": shots,
        "cover": {
            "title": truncate(points[0], 8),
            "subtitle": truncate(brief.get("title", "新品"), 16),
        },
        "caption": _caption(brief, platform, skill.name),
        "compliance": [],
        "source": "mock",
    }
    data["compliance"] = check_script(data)
    return data


def _repair_duration(shots: list[dict], duration: int) -> None:
    if not shots:
        return
    for shot in shots:
        try:
            shot["seconds"] = max(1.0, float(shot.get("seconds", 3)))
        except (TypeError, ValueError):
            shot["seconds"] = 3.0
    total = sum(float(shot["seconds"]) for shot in shots)
    diff = round(duration - total, 2)
    if abs(diff) < 0.01:
        return
    last = shots[-1]
    last["seconds"] = max(1.0, round(float(last["seconds"]) + diff, 2))
    remaining = round(duration - sum(float(shot["seconds"]) for shot in shots), 2)
    if abs(remaining) >= 0.01:
        shots[0]["seconds"] = max(1.0, round(float(shots[0]["seconds"]) + remaining, 2))


def validate_and_repair(data: dict, duration: int, ratio: str, platform: str) -> dict:
    data = dict(data or {})
    shots = data.get("shots")
    if not isinstance(shots, list) or not shots:
        raise AppError("SCRIPT_INVALID", "脚本没有可用镜头", status_code=502)
    if len(shots) > 8:
        shots = shots[:8]

    repaired: list[dict] = []
    for index, raw in enumerate(shots, start=1):
        shot = dict(raw or {})
        shot["idx"] = index
        shot["seconds"] = raw.get("seconds", 3)
        shot["beat"] = str(shot.get("beat", ""))
        shot["scene"] = str(shot.get("scene", "产品使用场景"))
        shot["camera"] = str(shot.get("camera", "中景固定"))
        shot["action"] = str(shot.get("action", shot["scene"]))
        shot["voiceover"] = truncate(str(shot.get("voiceover", "")), 60)
        shot["on_screen"] = truncate(str(shot.get("on_screen", "")), 18)
        shot["prompt"] = str(shot.get("prompt", ""))
        repaired.append(shot)

    _repair_duration(repaired, duration)
    data["shots"] = repaired
    data["duration"] = duration
    data["ratio"] = ratio
    data["platform"] = platform
    data.setdefault("title", "新品短视频")
    data.setdefault("cover", {"title": "新品", "subtitle": ""})
    data.setdefault("caption", {"title": "", "body": "", "hashtags": []})
    data.setdefault("style_bible", {})
    data["compliance"] = check_script(data)
    return data


def _persist_script(
    session: Session,
    project: Project,
    skill: Skill,
    platform: str,
    duration: int,
    ratio: str,
    data: dict,
) -> Script:
    version = session.query(Script).filter(Script.project_id == project.id).count() + 1
    script = Script(
        id=new_id("scr"),
        project_id=project.id,
        skill_id=skill.id,
        platform=platform,
        ratio=ratio,
        duration=duration,
        script_json=json_dumps(data),
        status="ready",
        version=version,
    )
    session.add(script)
    session.flush()

    for shot in data["shots"]:
        session.add(
            Shot(
                id=new_id("shot"),
                script_id=script.id,
                idx=int(shot["idx"]),
                seconds=float(shot["seconds"]),
                beat=shot.get("beat", ""),
                scene=shot.get("scene", ""),
                camera=shot.get("camera", ""),
                action=shot.get("action", ""),
                voiceover=shot.get("voiceover", ""),
                on_screen=shot.get("on_screen", ""),
                prompt=shot.get("prompt", ""),
                status="pending",
            )
        )
    project.status = "script_ready"
    session.commit()
    session.refresh(script)
    return script


async def generate_script(
    session: Session,
    project: Project,
    product: Product,
    skill: Skill,
    platform: str,
    duration: int,
    ratio: str,
    settings: Settings,
    *,
    goal: str = "转化",
) -> Script:
    if settings.llm_provider_name == "deepseek":
        if not settings.deepseek_configured:
            raise AppError("DEEPSEEK_KEY_MISSING", "DeepSeek 未配置 DEEPSEEK_API_KEY", status_code=400)
        from .providers.deepseek import DeepSeekClient

        brief = json_loads(product.brief_json, {})
        config = json_loads(skill.config_json, {})
        client = DeepSeekClient(settings)
        data = await client.write_script(brief, config, platform, duration, ratio, goal)
        data = validate_and_repair(data, duration, ratio, platform)
        data["skill_id"] = skill.id
        data["skill_version"] = skill.version
        data["source"] = "deepseek"
    elif settings.llm_provider_name == "ark":
        if not settings.ark_key_present:
            raise AppError("ARK_KEY_MISSING", "ark 模式需要配置 ARK_API_KEY", status_code=400)
        from .providers.ark import ArkClient

        brief = json_loads(product.brief_json, {})
        config = json_loads(skill.config_json, {})
        client = ArkClient(settings)
        data = await client.write_script(brief, config, platform, duration, ratio, goal)
        data = validate_and_repair(data, duration, ratio, platform)
        data["skill_id"] = skill.id
        data["skill_version"] = skill.version
        data["source"] = "ark"
    else:
        data = build_mock_script(project, product, skill, platform, duration, ratio)
        data = validate_and_repair(data, duration, ratio, platform)

    return _persist_script(session, project, skill, platform, duration, ratio, data)


def shot_to_dict(shot: Shot) -> dict:
    return {
        "id": shot.id,
        "idx": shot.idx,
        "seconds": shot.seconds,
        "beat": shot.beat,
        "scene": shot.scene,
        "camera": shot.camera,
        "action": shot.action,
        "voiceover": shot.voiceover,
        "on_screen": shot.on_screen,
        "prompt": shot.prompt,
        "status": shot.status,
        "error": shot.error,
        "keyframe_asset_id": shot.keyframe_asset_id,
        "subtitle_asset_id": shot.subtitle_asset_id,
        "clip_asset_id": shot.clip_asset_id,
    }


def script_detail(session: Session, script: Script) -> dict:
    data = json_loads(script.script_json, {})
    shots = session.query(Shot).filter(Shot.script_id == script.id).order_by(Shot.idx.asc()).all()
    if not shots:
        shots = []
    asset_ids = [sid for shot in shots for sid in (shot.keyframe_asset_id, shot.subtitle_asset_id, shot.clip_asset_id) if sid]
    assets = {
        asset.id: asset
        for asset in session.query(Asset).filter(Asset.id.in_(asset_ids)).all()
    } if asset_ids else {}

    shot_list = []
    for shot in shots:
        item = shot_to_dict(shot)
        for kind in ("keyframe", "subtitle", "clip"):
            asset_id = item.get(f"{kind}_asset_id")
            asset = assets.get(asset_id)
            item[f"{kind}_path"] = asset.path if asset else None
        shot_list.append(item)

    return {
        "id": script.id,
        "project_id": script.project_id,
        "skill_id": script.skill_id,
        "platform": script.platform,
        "ratio": script.ratio,
        "duration": script.duration,
        "status": script.status,
        "version": script.version,
        "skill_version": data.get("skill_version", 1),
        "title": data.get("title", ""),
        "style_bible": data.get("style_bible", {}),
        "cover": data.get("cover", {}),
        "caption": data.get("caption", {}),
        "compliance": data.get("compliance", []),
        "shots": shot_list,
        "source": data.get("source", "mock"),
    }


def patch_script(session: Session, script: Script, payload) -> dict:
    data = json_loads(script.script_json, {})
    updates = payload.model_dump(exclude_none=True)
    if "title" in updates:
        data["title"] = truncate(str(updates["title"]), 60)
    caption = data.get("caption") or {}
    if "caption_title" in updates:
        caption["title"] = str(updates["caption_title"])
    if "caption_body" in updates:
        caption["body"] = str(updates["caption_body"])
    if "hashtags" in updates:
        caption["hashtags"] = [str(tag).strip() for tag in updates["hashtags"] if str(tag).strip()]
    data["caption"] = caption

    if "shots" in updates:
        existing = {
            shot.idx: shot
            for shot in session.query(Shot).filter(Shot.script_id == script.id).all()
        }
        for patch in updates["shots"]:
            row = existing.get(int(patch["idx"]))
            if row is None:
                continue
            seconds = float(patch.get("seconds", row.seconds))
            visual_changed = (
                str(patch.get("scene", row.scene)) != str(row.scene)
                or str(patch.get("camera", row.camera)) != str(row.camera)
                or str(patch.get("action", row.action)) != str(row.action)
                or str(patch.get("prompt", row.prompt)) != str(row.prompt)
                or seconds != float(row.seconds)
            )
            text_changed = str(patch.get("on_screen", row.on_screen)) != str(row.on_screen)
            row.seconds = seconds
            row.scene = patch.get("scene", row.scene)
            row.camera = patch.get("camera", row.camera)
            row.action = patch.get("action", row.action)
            row.voiceover = truncate(patch.get("voiceover", row.voiceover), 60)
            row.on_screen = truncate(patch.get("on_screen", row.on_screen), 18)
            row.prompt = patch.get("prompt", row.prompt)
            # 只重置真正改动过的镜头资产，避免"改一句文案就丢掉全部分镜图"
            if visual_changed:
                row.keyframe_asset_id = None
                row.clip_asset_id = None
                row.status = "pending"
            if text_changed:
                row.subtitle_asset_id = None
                row.clip_asset_id = None
        session.flush()

        shots = session.query(Shot).filter(Shot.script_id == script.id).order_by(Shot.idx.asc()).all()
        shot_dicts = [shot_to_dict(shot) for shot in shots]
        _repair_duration(shot_dicts, script.duration)
        for shot, item in zip(shots, shot_dicts):
            shot.seconds = float(item["seconds"])
        data["shots"] = shot_dicts

    data = validate_and_repair(data, script.duration, script.ratio, script.platform)
    script.script_json = json_dumps(data)
    script.status = "ready"
    session.commit()
    return script_detail(session, script)
