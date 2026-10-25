r"""一条命令跑完「商品图 + 描述 → 风格 → 脚本 → 分镜图 → 成片」。

用法（PowerShell）：
  .\.venv\Scripts\python.exe scripts\pipeline.py --image path\to\product.jpg `
      --name "声波电动牙刷" --description "白色机身，五档模式，续航 30 天，128 元"

默认走 .env 里的真实 provider（DeepSeek + REST）。加 --mock 可零成本自检；
加 --no-video 只跑到分镜图，先看联系表再决定是否出片；
加 --script-id <id> 可从已有脚本继续跑（分镜图与成片都会复用已生成结果）。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.config import Settings  # noqa: E402
from backend.app.main import create_app  # noqa: E402


def _stage(title: str) -> float:
    print(f"\n=== {title} ===", flush=True)
    return time.time()


def _elapsed(started: float) -> str:
    return f"{time.time() - started:.1f}s"


def _prepare(client: TestClient, args: argparse.Namespace) -> tuple[str, dict, str]:
    """阶段 1-3：项目 / 商品解析 / 风格 / 脚本。返回 (project_id, script, style_id)。"""
    started = _stage("1/5 商品资料")
    project = client.post(
        "/api/projects",
        json={
            "name": args.name,
            "description": args.description,
            "platform": args.platform,
            "duration": args.duration,
            "ratio": "9:16",
        },
    )
    if project.status_code != 200:
        raise SystemExit(f"建项目失败：{project.status_code} {project.text[:400]}")
    project_id = project.json()["id"]

    image_path = Path(args.image).expanduser()
    with image_path.open("rb") as handle:
        upload = client.post(
            f"/api/projects/{project_id}/upload",
            files=[("files", (image_path.name, handle, "image/jpeg"))],
        )
    if upload.status_code != 200:
        raise SystemExit(f"上传失败：{upload.status_code} {upload.text[:400]}")
    print(f"project={project_id} image={image_path.name} {_elapsed(started)}")

    analyzed = client.post(f"/api/projects/{project_id}/analyze")
    if analyzed.status_code != 200:
        raise SystemExit(f"商品解析失败：{analyzed.status_code} {analyzed.text[:400]}")
    brief = analyzed.json()["brief"]
    print(
        f"title={brief.get('title')} category={brief.get('category')} "
        f"price={brief.get('price')} source={brief.get('source')}"
    )
    print(f"解析耗时 {_elapsed(started)}")

    started = _stage("2/5 视频风格")
    recommended = client.post("/api/skills/recommend", json={"project_id": project_id})
    items = recommended.json().get("items", []) if recommended.status_code == 200 else []
    style_id = args.style
    if style_id == "auto":
        if not items:
            raise SystemExit(f"风格推荐失败：{recommended.text[:400]}")
        style_id = items[0]["id"]
    print("推荐：", [(item["id"], item.get("reason", "")) for item in items])
    print(f"选用 style={style_id}")

    started = _stage("3/5 脚本与分镜表")
    script_resp = client.post(
        f"/api/projects/{project_id}/scripts",
        json={
            "skill_id": style_id,
            "platform": args.platform,
            "duration": args.duration,
            "ratio": "9:16",
        },
    )
    if script_resp.status_code != 200:
        raise SystemExit(f"脚本生成失败：{script_resp.status_code} {script_resp.text[:400]}")
    script = script_resp.json()
    print(f"script={script['id']} source={script['source']} shots={len(script['shots'])}")
    for shot in script["shots"]:
        print(f"  {shot['idx']} {shot['seconds']}s {shot['scene'][:40]} | 字幕 {shot['on_screen']}")
    print(f"脚本耗时 {_elapsed(started)}")
    return project_id, script, style_id


def _storyboards(client: TestClient, project_id: str, script: dict) -> list[str]:
    started = _stage("4/5 分镜图")
    previs = client.post(f"/api/scripts/{script['id']}/previs")
    if previs.status_code != 200:
        raise SystemExit(f"分镜图失败：{previs.status_code} {previs.text[:400]}")
    generation = previs.json()
    print(f"previs status={generation['status']} {generation.get('error') or ''}")
    print(f"result={json.dumps(generation.get('result'), ensure_ascii=False)}")
    print(f"分镜耗时 {_elapsed(started)}")

    detail = client.get(f"/api/projects/{project_id}").json()
    paths = [shot["keyframe_path"] for shot in detail.get("script", {}).get("shots", [])]
    print("分镜文件：", paths)
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description="商品图 + 描述 → 成片")
    parser.add_argument("--image", default="", help="产品图路径（--script-id 时可省略）")
    parser.add_argument("--name", default="", help="商品名")
    parser.add_argument("--description", default="", help="商品描述（卖点、规格、价格）")
    parser.add_argument("--style", default="auto", help="风格 Skill id，默认自动推荐 Top1")
    parser.add_argument("--platform", default="douyin")
    parser.add_argument("--duration", type=int, default=15)
    parser.add_argument("--script-id", default="", help="从已有脚本继续（复用分镜图与成片）")
    parser.add_argument("--no-video", action="store_true", help="只生成脚本与分镜图")
    parser.add_argument("--mock", action="store_true", help="强制 mock（不调外部模型）")
    args = parser.parse_args()

    if args.script_id:
        if not args.image and not args.name:
            # resume 不需要图和描述，这里只是避免误报
            pass
    else:
        if not args.image or not args.name or not args.description:
            parser.error("首次运行需要 --image / --name / --description")
        if not Path(args.image).expanduser().exists():
            raise SystemExit(f"找不到产品图：{args.image}")

    overrides: dict = {"sync_jobs": True}
    if args.mock:
        overrides.update(ai_mode="mock", llm_provider="mock", image_provider="mock", video_provider="mock")
    settings = Settings(**overrides)
    app = create_app(settings)

    with TestClient(app) as client:
        if args.script_id:
            script = client.get(f"/api/scripts/{args.script_id}").json()
            if "id" not in script:
                raise SystemExit(f"找不到脚本：{args.script_id}")
            project_id = script["project_id"]
            style_id = args.style if args.style != "auto" else script["skill_id"]
            print(f"resume script={script['id']} project={project_id} style={style_id}")
            print(f"shots={len(script['shots'])}")
        else:
            project_id, script, style_id = _prepare(client, args)

        _storyboards(client, project_id, script)
        if args.no_video:
            print("\n已跳过视频生成（--no-video）。")
            return 0

        started = _stage("5/5 成片")
        render = client.post(f"/api/scripts/{script['id']}/render")
        if render.status_code != 200:
            raise SystemExit(f"成片失败：{render.status_code} {render.text[:400]}")
        generation = render.json()
        print(f"render status={generation['status']} {generation.get('error') or ''}")
        print(f"成片耗时 {_elapsed(started)}")
        if generation["status"] != "succeeded":
            return 1

        video_resp = client.get(f"/api/projects/{project_id}/video")
        if video_resp.status_code != 200:
            raise SystemExit(f"读取成片失败：{video_resp.text[:400]}")
        payload = video_resp.json()
        print(
            f"video={payload.get('path')} {payload.get('width')}x{payload.get('height')} "
            f"{payload.get('duration')}s"
        )
        print("url:", payload.get("url"))

        summary = {
            "project_id": project_id,
            "script_id": script["id"],
            "style_id": style_id,
            "video": payload.get("path"),
            "cover": (payload.get("cover") or {}).get("path"),
            "contact_sheet": f"projects/{project_id}/storyboards/contact_sheet.jpg",
        }
        (settings.data_path / "pipeline_last.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("\n完成：", json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
