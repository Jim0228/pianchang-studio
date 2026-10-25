from __future__ import annotations

import json
import shutil
import time
import zipfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8000"
SCRIPT_ID = "scr_ad0c00bb6bb2"
PROJECT_ID = "prj_3f03d4e5fbac"


def main() -> int:
    response = httpx.post(f"{BASE}/api/scripts/{SCRIPT_ID}/render", timeout=60)
    response.raise_for_status()
    generation = response.json()
    print("generation", generation["id"], generation["stage"], flush=True)

    deadline = time.time() + 40 * 60
    current = generation
    while time.time() < deadline:
        time.sleep(10)
        current = httpx.get(f"{BASE}/api/generations/{generation['id']}").json()
        print(
            f"{current['progress']:>3}% {current['status']:<10} {current.get('message', '')}",
            flush=True,
        )
        if current["status"] in {"succeeded", "failed"}:
            break
    print(json.dumps(current, ensure_ascii=False, indent=2))
    if current["status"] != "succeeded":
        return 1

    detail = httpx.get(f"{BASE}/api/projects/{PROJECT_ID}").json()
    video = detail["video"]
    print("video", json.dumps(video, ensure_ascii=False)[:1000])
    export = httpx.post(f"{BASE}/api/videos/{video['id']}/export", timeout=120).json()
    payload = httpx.get(f"{BASE}{export['download_url']}", timeout=300)
    payload.raise_for_status()
    zip_path = ROOT / "data" / "demo_rest_export.zip"
    zip_path.write_bytes(payload.content)
    out = ROOT / "data" / "demo_rest"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(out)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    print("zip", zip_path.stat().st_size, "files", sorted(path.name for path in out.iterdir()))
    print("manifest", manifest.get("mode"), manifest.get("video_provider"), manifest.get("ai"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
