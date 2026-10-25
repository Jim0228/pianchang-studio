from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CFG = dotenv_values(ROOT / ".env")
MCP_URL = CFG.get("MCP_VIDEO_URL", "")
TOKEN = CFG.get("MCP_VIDEO_TOKEN", "")
BASE = MCP_URL.rsplit("/mcp", 1)[0] if MCP_URL.endswith("/mcp") else MCP_URL
if not BASE or not TOKEN:
    raise SystemExit("缺少 MCP_VIDEO_URL / MCP_VIDEO_TOKEN")

HEADERS = {
    "Authorization": TOKEN if TOKEN.lower().startswith("bearer ") else f"Bearer {TOKEN}",
    "Content-Type": "application/json",
}
JOB_FILE = ROOT / "data" / "rest_job.json"


def post(path: str, payload: dict, timeout: float = 180.0) -> dict:
    try:
        response = httpx.post(f"{BASE}{path}", headers=HEADERS, json=payload, timeout=timeout)
    except httpx.HTTPError as exc:
        raise SystemExit(f"HTTP 请求失败：{exc}") from exc
    try:
        data = response.json()
    except Exception:
        raise SystemExit(f"非 JSON 响应 {response.status_code}：{response.text[:800]}")
    if response.status_code >= 400 or data.get("error"):
        error = data.get("error") or {}
        message = error.get("message") if isinstance(error, dict) else str(error)
        raise SystemExit(f"接口失败 {response.status_code}：{message or data}")
    return data


def summarize(job: dict) -> dict:
    return {
        "job_id": job.get("job_id"),
        "kind": job.get("kind"),
        "model": job.get("model"),
        "status": job.get("status"),
        "progress": job.get("progress"),
        "error": job.get("error"),
        "params": job.get("params"),
        "inputs": [
            {
                "kind": item.get("kind"),
                "content_type": item.get("content_type"),
                "width": item.get("width"),
                "height": item.get("height"),
                "status": item.get("status"),
            }
            for item in (job.get("inputs") or [])
        ],
        "results": [
            {
                "kind": item.get("kind"),
                "content_type": item.get("content_type"),
                "status": item.get("status"),
                "remote_url": item.get("remote_url"),
                "download_url": item.get("download_url"),
            }
            for item in (job.get("results") or [])
        ],
    }


def data_url(path: Path) -> str:
    suffix = path.suffix.lower()
    mime = "image/png" if suffix == ".png" else "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def find_images() -> list[Path]:
    project = Path("data/projects/prj_3f03d4e5fbac")
    plate = project / "plates" / "shot_01.jpg"
    originals = sorted(
        path
        for path in (project / "originals").glob("*.jpg")
        if not path.name.endswith("_thumb.jpg")
    )
    paths = [path for path in [plate, *originals[:1]] if path.exists()]
    if not paths:
        fallback = Path("data/demo/cover.jpg")
        if fallback.exists():
            paths = [fallback]
    return paths


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("step", choices=["list", "generate", "poll", "result"])
    parser.add_argument("--wait", type=int, default=60)
    args = parser.parse_args()

    if args.step == "list":
        data = post(
            "/v1/capabilities/generation/generation_list",
            {"kind": "video", "limit": 5},
        )
        print("total:", data.get("total"))
        for item in data.get("items", []):
            print(
                item.get("job_id"),
                item.get("kind"),
                item.get("status"),
                item.get("params"),
                (item.get("results") or [{}])[0].get("remote_url"),
            )
        return 0

    if args.step == "generate":
        images = find_images()
        if not images:
            raise SystemExit("找不到参考图")
        prompt = (
            "Use the first provided image as the exact first frame. "
            "Animate a subtle slow push-in on the product, keep the product shape, proportions, "
            "color, label and composition unchanged, soft natural window light, photorealistic "
            "e-commerce commercial quality, no text, no letters, no watermark, no extra objects."
        )
        payload = {
            "prompt": prompt,
            "params": {"aspectRatio": "portrait", "resolution": "480p", "duration": 4},
            "images": [data_url(path) for path in images],
            "wait_seconds": 20,
        }
        print("images:", [str(path) for path in images])
        job = post("/v1/capabilities/generation/video_generate", payload)
        JOB_FILE.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(summarize(job), ensure_ascii=False, indent=2))
        return 0

    if not JOB_FILE.exists():
        raise SystemExit("没有 data/rest_job.json，请先执行 generate")
    saved = json.loads(JOB_FILE.read_text(encoding="utf-8"))
    job_id = saved.get("job_id")
    if not job_id:
        raise SystemExit("job_id 缺失")

    if args.step == "poll":
        job = post(
            "/v1/capabilities/generation/generation_job",
            {"job_id": job_id, "wait_seconds": args.wait},
            timeout=args.wait + 60,
        )
        JOB_FILE.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(summarize(job), ensure_ascii=False, indent=2))
        return 0

    job = post(
        "/v1/capabilities/generation/generation_result",
        {"job_id": job_id},
        timeout=60,
    )
    JOB_FILE.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summarize(job), ensure_ascii=False, indent=2))
    results = job.get("results") or []
    if not results:
        raise SystemExit("没有结果文件")
    url = results[0].get("download_url") or results[0].get("remote_url")
    if not url:
        raise SystemExit("结果缺少下载地址")
    out = ROOT / "data" / "rest_video_test.mp4"
    response = httpx.get(url, timeout=300, follow_redirects=True)
    response.raise_for_status()
    out.write_bytes(response.content)
    print("saved:", out, out.stat().st_size)
    from backend.app.config import Settings
    from backend.app.services.ffmpeg import probe

    print("probe:", probe(Settings(), out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
