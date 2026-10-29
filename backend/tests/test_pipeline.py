from __future__ import annotations

import io
import json
import wave
import zipfile

from PIL import Image, ImageDraw

from backend.app.models import Shot
from backend.app.services.compliance import check_text
from backend.app.services.music import generate_bgm
from backend.app.services.script import _fit_voiceover
from backend.app.services.storyboard import (
    IDENTITY_LOCK,
    build_storyboard_prompt,
    build_video_prompt,
)


def make_product_image() -> bytes:
    image = Image.new("RGB", (600, 600), "#F3E7D3")
    draw = ImageDraw.Draw(image)
    for y in range(600):
        ratio = y / 599
        draw.line(
            [(0, y), (600, y)],
            fill=(
                int(243 * (1 - ratio) + 201 * ratio),
                int(231 * (1 - ratio) + 176 * ratio),
                int(211 * (1 - ratio) + 140 * ratio),
            ),
        )
    draw.rounded_rectangle([170, 130, 430, 470], radius=34, fill="#2E2A26")
    draw.rounded_rectangle([200, 160, 400, 270], radius=18, fill="#F6F1E7")
    draw.ellipse([270, 320, 330, 380], fill="#FF6B3D")
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=92)
    return buffer.getvalue()


def test_health_skills_and_platforms(client):
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["ai_mode"] == "mock"

    skills = client.get("/api/skills")
    assert skills.status_code == 200
    assert len(skills.json()) == 13

    platforms = client.get("/api/platforms")
    assert platforms.status_code == 200
    assert {item["id"] for item in platforms.json()} >= {"douyin", "xiaohongshu", "xianyu"}


def test_full_mock_pipeline_and_export(client):
    project = client.post(
        "/api/projects",
        json={
            "name": "厨房油污清洁剂",
            "description": "500ml 植物配方，不伤手，一喷一擦，厨房灶台油烟机都能用，39.9 元",
            "platform": "douyin",
            "duration": 15,
            "ratio": "9:16",
        },
    )
    assert project.status_code == 200
    project_id = project.json()["id"]

    upload = client.post(
        f"/api/projects/{project_id}/upload",
        files=[("files", ("product.jpg", make_product_image(), "image/jpeg"))],
    )
    assert upload.status_code == 200
    assert upload.json()[0]["kind"] == "original"

    analyzed = client.post(f"/api/projects/{project_id}/analyze")
    assert analyzed.status_code == 200
    product = analyzed.json()
    assert product["title"] == "厨房油污清洁剂"
    assert product["brief"]["price"] == "39.9"

    recommend = client.post("/api/skills/recommend", json={"project_id": project_id})
    assert recommend.status_code == 200
    items = recommend.json()["items"]
    assert len(items) == 3
    assert items[0]["id"] == "pain_point_reversal"

    script_resp = client.post(
        f"/api/projects/{project_id}/scripts",
        json={"skill_id": items[0]["id"], "platform": "douyin", "duration": 15, "ratio": "9:16"},
    )
    assert script_resp.status_code == 200
    script = script_resp.json()
    assert len(script["shots"]) == 5
    assert sum(float(shot["seconds"]) for shot in script["shots"]) == 15

    patched = client.patch(
        f"/api/scripts/{script['id']}",
        json={"title": "厨房油污清洁剂｜全网最好用"},
    )
    assert patched.status_code == 200
    assert any(item["word"].startswith("最好") for item in patched.json()["compliance"])

    previs = client.post(f"/api/scripts/{script['id']}/previs")
    assert previs.status_code == 200
    generation = previs.json()
    assert generation["status"] == "succeeded", generation.get("error")

    detail = client.get(f"/api/projects/{project_id}").json()
    assert all(shot["keyframe_path"] for shot in detail["script"]["shots"])

    render = client.post(f"/api/scripts/{script['id']}/render")
    assert render.status_code == 200
    generation = render.json()
    assert generation["status"] == "succeeded", generation.get("error")

    detail = client.get(f"/api/projects/{project_id}").json()
    video = detail["video"]
    assert video is not None
    assert video["width"] == 540 and video["height"] == 960
    assert 14 <= video["duration"] <= 16

    export = client.post(f"/api/videos/{video['id']}/export")
    assert export.status_code == 200
    export_data = export.json()
    download = client.get(export_data["download_url"])
    assert download.status_code == 200
    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
        names = set(archive.namelist())
        assert {
            "final.mp4",
            "cover.jpg",
            "script.json",
            "subtitles.srt",
            "caption.md",
            "manifest.json",
        } <= names
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["mode"] == "mock"
        assert manifest["ai"]["label"] == "AI 生成"
        assert manifest["skill"]["version"] == script["version"]
        srt = archive.read("subtitles.srt").decode("utf-8")
        assert "00:00:00,000" in srt


def test_compliance_does_not_flag_last():
    assert check_text("最后一步，收尾动作", "voiceover") == []
    hits = check_text("全网最好用，100%有效", "caption")
    assert any(item["word"] == "最好" for item in hits)
    assert any(item["level"] == "block" for item in hits)


def test_storyboard_prompt_locks_identity_and_bans_text():
    shot = Shot(
        idx=2,
        scene="浴室台面上的白色电动牙刷特写",
        camera="缓慢推近",
        prompt="真实商业摄影质感，浅景深",
    )
    brief = {
        "title": "声波电动牙刷",
        "category": "个护",
        "product_visual": {"body_color": "white", "screen": "black round display"},
    }
    config = {
        "prompt": {
            "style": "Photorealistic e-commerce commercial photography",
            "lighting": "soft morning window light",
            "negative": "no extra products, no deformed label",
            "palette": ["#F6F1E7", "#2E2A26"],
        }
    }

    prompt = build_storyboard_prompt(shot, brief, config, "9:16")
    assert IDENTITY_LOCK in prompt
    assert "white" in prompt and "black round display" in prompt
    assert "浴室台面上的白色电动牙刷特写" in prompt
    assert "no text" in prompt.lower()
    assert "no extra products" in prompt
    assert "9:16" in prompt

    video = build_video_prompt(shot, config)
    assert "exact first frame" in video
    assert "缓慢推近" in video
    assert "no morphing" in video


def _project_with_script(client):
    project = client.post(
        "/api/projects",
        json={
            "name": "声波电动牙刷",
            "description": "白色机身，五档模式，IPX7 防水，续航 30 天，128 元",
            "platform": "douyin",
            "duration": 15,
            "ratio": "9:16",
        },
    ).json()
    project_id = project["id"]
    client.post(
        f"/api/projects/{project_id}/upload",
        files=[("files", ("product.jpg", make_product_image(), "image/jpeg"))],
    )
    client.post(f"/api/projects/{project_id}/analyze")
    recommended = client.post("/api/skills/recommend", json={"project_id": project_id}).json()["items"][0]
    script = client.post(
        f"/api/projects/{project_id}/scripts",
        json={"skill_id": recommended["id"], "platform": "douyin", "duration": 15, "ratio": "9:16"},
    ).json()
    client.post(f"/api/scripts/{script['id']}/previs")
    detail = client.get(f"/api/projects/{project_id}").json()
    return project_id, detail


def test_regenerate_one_shot_keeps_other_storyboards(client):
    project_id, detail = _project_with_script(client)
    shots = detail["script"]["shots"]
    assert shots and all(shot["keyframe_path"] for shot in shots)

    payload = {
        "shots": [
            {
                "idx": shot["idx"],
                "seconds": shot["seconds"],
                "scene": shot["scene"],
                "camera": shot["camera"],
                "action": shot["action"],
                "voiceover": shot["voiceover"],
                "on_screen": shot["on_screen"],
                "prompt": shot["prompt"],
            }
            for shot in shots
        ]
    }
    payload["shots"][0]["prompt"] = "改成侧逆光微距，突出瓶身质感"
    patched = client.patch(f"/api/scripts/{detail['script']['id']}", json=payload).json()
    assert patched["shots"][0]["keyframe_path"] is None
    assert all(shot["keyframe_path"] for shot in patched["shots"][1:])

    generation = client.post(f"/api/shots/{shots[0]['id']}/regenerate").json()
    assert generation["status"] == "succeeded", generation.get("error")

    after = client.get(f"/api/projects/{project_id}").json()["script"]["shots"]
    assert all(shot["keyframe_path"] for shot in after)
    assert after[0]["keyframe_path"] == f"projects/{project_id}/keyframes/shot_01.jpg"


def test_voiceover_is_trimmed_to_shot_budget():
    long_line = "这一句口播非常长，如果原样念下去一定会压到下一个镜头，所以必须在脚本层就裁掉多余的部分"
    fitted = _fit_voiceover(long_line, 3.0)
    assert len(fitted) <= 13
    assert _fit_voiceover("一喷一擦", 3.0) == "一喷一擦"


def test_longer_duration_scales_shots_and_subtitle_matches_voiceover(client):
    project = client.post(
        "/api/projects",
        json={
            "name": "声波电动牙刷 30 秒",
            "description": "白色机身，五档模式，IPX7 防水，续航 30 天，128 元",
            "platform": "douyin",
            "duration": 30,
            "ratio": "9:16",
        },
    ).json()
    project_id = project["id"]
    client.post(
        f"/api/projects/{project_id}/upload",
        files=[("files", ("product.jpg", make_product_image(), "image/jpeg"))],
    )
    client.post(f"/api/projects/{project_id}/analyze")
    skill = client.post("/api/skills/recommend", json={"project_id": project_id}).json()["items"][0]
    script = client.post(
        f"/api/projects/{project_id}/scripts",
        json={"skill_id": skill["id"], "platform": "douyin", "duration": 30, "ratio": "9:16"},
    ).json()

    shots = script["shots"]
    assert script["duration"] == 30
    assert len(shots) >= 4
    assert abs(sum(float(shot["seconds"]) for shot in shots) - 30) < 0.01
    for shot in shots:
        assert 2 <= float(shot["seconds"]) <= 15
        assert shot["on_screen"] == shot["voiceover"]



def test_bgm_matches_requested_duration(tmp_path):
    path = generate_bgm(tmp_path / "bgm.wav", 4.0, "warm")
    with wave.open(str(path), "rb") as handle:
        duration = handle.getnframes() / float(handle.getframerate())
    assert abs(duration - 4.0) < 0.15


def test_presenter_and_continuity_reach_storyboard_prompt():
    shot = Shot(idx=3, scene="主播拿起产品演示", camera="近景", action="按下开关")
    brief = {"title": "声波电动牙刷", "category": "个护"}
    config = {
        "prompt": {"style": "真实生活质感", "lighting": "窗边柔光", "negative": "变形"},
        "presenter": {
            "enabled": True,
            "type": "host",
            "description": "28 岁亚洲女性，浅灰针织衫，低马尾",
            "framing": "半身中景",
        },
        "continuity": {
            "mode": "continuous_action",
            "action_arc": ["拿起", "按下开关", "展示结果"],
            "rules": ["同一人同一套衣服", "动作接续上一镜"],
        },
    }

    prompt = build_storyboard_prompt(shot, brief, config, "9:16", has_previous=True)
    assert "SAME PERSON IN EVERY SHOT" in prompt
    assert "浅灰针织衫" in prompt
    assert "CONTINUITY RULES" in prompt
    assert "1) 拿起" in prompt
    assert "third reference image is the previous shot" in prompt
    assert "no people" not in prompt.lower()

    video = build_video_prompt(shot, config)
    assert "same person" in video.lower()
    assert "按下开关" in video


