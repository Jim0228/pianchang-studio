"""End-to-end smoke test for the Mock pipeline.

Run from the repository root:
    .\\.venv\\Scripts\\python.exe scripts\\smoke.py
"""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path

os.environ.setdefault("SYNC_JOBS", "true")

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.config import Settings  # noqa: E402
from backend.app.main import create_app  # noqa: E402


def make_product_image() -> bytes:
    image = Image.new("RGB", (900, 900), "#F3E7D3")
    draw = ImageDraw.Draw(image)
    for y in range(900):
        ratio = y / 899
        color = (
            int(243 * (1 - ratio) + 201 * ratio),
            int(231 * (1 - ratio) + 176 * ratio),
            int(211 * (1 - ratio) + 140 * ratio),
        )
        draw.line([(0, y), (900, y)], fill=color)
    draw.rounded_rectangle([260, 190, 640, 710], radius=48, fill="#2E2A26")
    draw.rounded_rectangle([300, 230, 600, 400], radius=24, fill="#F6F1E7")
    draw.ellipse([410, 470, 490, 550], fill="#FF6B3D")
    draw.text((300, 760), "DEMO PRODUCT", fill="#2E2A26")
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=92)
    return buffer.getvalue()


def main() -> int:
    settings = Settings(
        sync_jobs=True,
        ai_mode="mock",
        llm_provider="mock",
        image_provider="mock",
        video_provider="mock",
    )
    app = create_app(settings)
    client = TestClient(app)

    health = client.get("/api/health").json()
    print("[health]", health["status"], health["ai_mode"], health["ffmpeg"].get("codec"))

    project_resp = client.post(
        "/api/projects",
        json={
            "name": "厨房油污清洁剂",
            "description": "500ml 植物配方，不伤手，一喷一擦，厨房灶台油烟机都能用，39.9 元",
            "platform": "douyin",
            "duration": 15,
            "ratio": "9:16",
        },
    )
    project_resp.raise_for_status()
    project = project_resp.json()
    project_id = project["id"]
    print("[project]", project_id, project["name"])

    upload = client.post(
        f"/api/projects/{project_id}/upload",
        files=[("files", ("product.jpg", make_product_image(), "image/jpeg"))],
    )
    upload.raise_for_status()
    print("[upload]", len(upload.json()))

    analyze = client.post(f"/api/projects/{project_id}/analyze")
    analyze.raise_for_status()
    brief = analyze.json()["brief"]
    print("[brief]", brief["category"], brief["selling_points"][:3], "price=", brief.get("price"))

    recommend = client.post("/api/skills/recommend", json={"project_id": project_id})
    recommend.raise_for_status()
    items = recommend.json()["items"]
    print("[recommend]", [(item["id"], item["score"]) for item in items])
    skill_id = items[0]["id"]

    script_resp = client.post(
        f"/api/projects/{project_id}/scripts",
        json={"skill_id": skill_id, "platform": "douyin", "duration": 15, "ratio": "9:16"},
    )
    script_resp.raise_for_status()
    script = script_resp.json()
    print("[script]", script["id"], len(script["shots"]), script["duration"])

    previs = client.post(f"/api/scripts/{script['id']}/previs")
    previs.raise_for_status()
    gen = previs.json()
    print("[previs]", gen["status"], gen["progress"], gen["error"][:200])
    if gen["status"] != "succeeded":
        print(gen)
        return 1

    render = client.post(f"/api/scripts/{script['id']}/render")
    render.raise_for_status()
    gen = render.json()
    print("[render]", gen["status"], gen["progress"], gen["error"][:300])
    if gen["status"] != "succeeded":
        print(gen)
        return 1

    detail = client.get(f"/api/projects/{project_id}").json()
    video = detail["video"]
    print("[video]", video["url"], video["width"], video["height"], video["duration"])

    export = client.post(f"/api/videos/{video['id']}/export")
    export.raise_for_status()
    export_data = export.json()
    print("[export]", export_data["filename"], export_data["size"])

    download = client.get(export_data["download_url"])
    download.raise_for_status()
    out = ROOT / "data" / "demo_export.zip"
    out.write_bytes(download.content)
    print("[saved]", out, out.stat().st_size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
