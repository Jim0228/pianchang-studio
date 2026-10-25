from __future__ import annotations

from pathlib import Path

import yaml

PRESET_PATH = Path(__file__).resolve().parents[1] / "presets" / "platforms.yaml"
_cache: dict | None = None


def _load() -> dict:
    global _cache
    if _cache is None:
        with PRESET_PATH.open("r", encoding="utf-8") as handle:
            _cache = yaml.safe_load(handle) or {}
    return _cache


def list_platforms() -> list[dict]:
    data = _load()
    result = []
    for key, value in data.items():
        result.append({"id": key, **value})
    return result


def get_platform(platform_id: str) -> dict:
    data = _load()
    if platform_id in data:
        return {"id": platform_id, **data[platform_id]}
    return {"id": platform_id, **data["douyin"]}
