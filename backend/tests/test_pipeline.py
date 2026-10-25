from __future__ import annotations

import io
import json
import zipfile

from PIL import Image, ImageDraw

from backend.app.models import Shot
from backend.app.services.compliance import check_text
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
    assert len(skills.json()) == 6

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

