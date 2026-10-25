from __future__ import annotations

import asyncio
import base64
from pathlib import Path

import httpx

from ...config import Settings
from ...errors import AppError


def _data_url(path: Path) -> str:
    suffix = path.suffix.lower()
    mime = "image/png" if suffix == ".png" else "image/jpeg"
    payload = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{payload}"


class SeedanceClient:
    def __init__(self, settings: Settings):
        if not settings.ark_key_present:
            raise AppError("ARK_KEY_MISSING", "real 模式需要配置 ARK_API_KEY", status_code=400)
        self.settings = settings
        self.base_url = settings.ark_base_url.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {settings.ark_api_key}",
            "Content-Type": "application/json",
        }

    async def _create_task(self, payload: dict) -> str:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=20.0)) as client:
            response = await client.post(f"{self.base_url}/contents/generations/tasks", headers=self.headers, json=payload)
        if response.status_code >= 400:
            raise AppError(
                "PROVIDER_ERROR",
                f"Seedance 创建任务失败 {response.status_code}：{response.text[:500]}",
                status_code=502,
                retryable=response.status_code in {429, 500, 502, 503, 504},
            )
        data = response.json()
        task_id = data.get("id") or data.get("task_id") or (data.get("data") or {}).get("id")
        if not task_id:
            raise AppError("PROVIDER_ERROR", "Seedance 未返回任务 ID", status_code=502)
        return str(task_id)

    async def generate_video(
        self,
        prompt: str,
        first_frame: Path | None,
        out_path: Path,
        *,
        seconds: float,
        ratio: str,
    ) -> dict:
        content: list[dict] = [{"type": "text", "text": prompt}]
        if first_frame is not None and first_frame.exists():
            content.append({"type": "image_url", "image_url": {"url": _data_url(first_frame)}})
        payload = {
            "model": self.settings.ark_video_model,
            "content": content,
            "ratio": ratio,
            "duration": max(1, int(round(seconds))),
            "resolution": "1080p",
            "watermark": False,
        }
        task_id = await self._create_task(payload)
        video_url = await self._poll(task_id)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                response = await client.get(video_url)
                response.raise_for_status()
                out_path.write_bytes(response.content)
        except httpx.HTTPError as exc:
            raise AppError("PROVIDER_ERROR", f"下载视频失败：{exc}", status_code=502) from exc
        return {"task_id": task_id, "url": video_url, "path": str(out_path)}

    async def _poll(self, task_id: str) -> str:
        deadline = asyncio.get_running_loop().time() + 900
        url = f"{self.base_url}/contents/generations/tasks/{task_id}"
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=20.0)) as client:
            while asyncio.get_running_loop().time() < deadline:
                response = await client.get(url, headers=self.headers)
                if response.status_code >= 400:
                    raise AppError("PROVIDER_ERROR", f"Seedance 查询失败 {response.status_code}", status_code=502)
                data = response.json()
                status = str(data.get("status", "")).lower()
                if status in {"succeeded", "success", "completed"}:
                    content = data.get("content") or {}
                    video_url = content.get("video_url") or data.get("video_url") or (data.get("data") or {}).get("video_url")
                    if not video_url:
                        raise AppError("PROVIDER_ERROR", "Seedance 成功但未返回视频地址", status_code=502)
                    return str(video_url)
                if status in {"failed", "error", "cancelled"}:
                    raise AppError("PROVIDER_ERROR", f"Seedance 任务失败：{data.get('error') or data}", status_code=502)
                await asyncio.sleep(5)
        raise AppError("PROVIDER_ERROR", "Seedance 任务超时", status_code=504, retryable=True)
