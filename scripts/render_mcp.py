from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BASE = "http://127.0.0.1:8000"

project = httpx.get(f"{BASE}/api/projects").json()[0]
detail = httpx.get(f"{BASE}/api/projects/{project['id']}").json()
script_id = detail["script"]["id"]
response = httpx.post(f"{BASE}/api/scripts/{script_id}/render", timeout=60)
response.raise_for_status()
generation = response.json()
print("generation", generation["id"], "stage", generation["stage"], flush=True)

deadline = time.time() + 30 * 60
while time.time() < deadline:
    time.sleep(10)
    current = httpx.get(f"{BASE}/api/generations/{generation['id']}").json()
    print(
        f"{current['progress']:>3}% {current['status']:<10} {current.get('message', '')}",
        flush=True,
    )
    if current["status"] in {"succeeded", "failed"}:
        print(json.dumps(current, ensure_ascii=False, indent=2))
        if current["status"] == "succeeded":
            detail = httpx.get(f"{BASE}/api/projects/{project['id']}").json()
            print("video", json.dumps(detail.get("video"), ensure_ascii=False))
        break
else:
    print("timeout")
