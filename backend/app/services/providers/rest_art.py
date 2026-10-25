from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Any

import httpx

from ...config import Settings
from ...errors import AppError


class RestArtClient:
    """REST client for the gateway's generation capabilities."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.base = settings.rest_art_base_url.strip().rstrip("/")
        self.token = settings.rest_art_token.strip()
        if not self.base or not self.token:
            raise AppError(
                "REST_NOT_CONFIGURED",
                "REST 美术资源未配置：请设置 REST_ART_BASE_URL 与 REST_ART_TOKEN",
                status_code=400,
            )
        self.headers = {
            "Authorization": self.token if self.token.lower().startswith("bearer ") else f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def _data_url(self, path: Path) -> str:
        suffix = path.suffix.lower()
        mime = "image/png" if suffix == ".png" else "image/jpeg"
        return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")

    async def _post(
        self,
        client: httpx.AsyncClient,
        path: str,
        payload: dict,
        *,
        timeout: float = 180.0,
    ) -> dict:
        try:
            response = await asyncio.wait_for(
                client.post(
                    f"{self.base}{path}",
                    headers=self.headers,
                    json=payload,
                    timeout=timeout,
                ),
                timeout=timeout + 30,
            )
        except asyncio.TimeoutError as exc:
            raise AppError(
                "REST_ERROR",
                f"REST 请求超时（{timeout + 30:.0f}s）：{path}",
                status_code=504,
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise AppError("REST_ERROR", f"REST 请求失败：{exc}", status_code=502, retryable=True) from exc
        try:
            data = response.json()
        except Exception as exc:  # noqa: BLE001
            raise AppError("REST_ERROR", f"REST 返回非 JSON（{response.status_code}）", status_code=502) from exc
        if response.status_code >= 400 or data.get("error"):
            error = data.get("error") or {}
            message = error.get("message") if isinstance(error, dict) else str(error)
            raise AppError(
                "REST_ERROR",
                f"REST 接口失败 {response.status_code}：{message or data}",
                status_code=502,
                retryable=response.status_code in {429, 500, 502, 503, 504},
            )
        return data

    @staticmethod
    def _aspect(ratio: str) -> str:
        if ratio in {"9:16", "3:4"}:
            return "portrait"
        if ratio in {"16:9"}:
            return "landscape"
        return "square"

    @staticmethod
    def _ready_results(job: dict) -> list[dict]:
        return [
            item
            for item in (job.get("results") or [])
            if item.get("status") == "ready"
            and (item.get("download_url") or item.get("remote_url"))
        ]

    async def _wait(self, client: httpx.AsyncClient, job: dict) -> dict:
        job_id = job.get("job_id")
        if not job_id:
            raise AppError("REST_ERROR", "REST 未返回 job_id", status_code=502)
        deadline = asyncio.get_running_loop().time() + max(60, self.settings.rest_art_timeout)
        poll_seconds = max(5, min(120, self.settings.rest_video_wait_seconds))

        while asyncio.get_running_loop().time() < deadline:
            status = str(job.get("status", "")).lower()
            if status == "violation":
                raise AppError("REST_VIOLATION", f"内容审核未通过：{job.get('error') or ''}", status_code=400)
            if status in {"failed", "error", "cancelled"}:
                raise AppError("REST_ERROR", f"生成失败：{job.get('error') or status}", status_code=502)

            ready = self._ready_results(job)
            if status in {"succeeded", "success", "completed"} and ready:
                return job
            if status in {"succeeded", "success", "completed"} and not ready:
                await asyncio.sleep(2)
                job = await self._post(
                    client,
                    "/v1/capabilities/generation/generation_result",
                    {"job_id": job_id},
                    timeout=90,
                )
                continue

            job = await self._post(
                client,
                "/v1/capabilities/generation/generation_job",
                {"job_id": job_id, "wait_seconds": poll_seconds},
                timeout=poll_seconds + 90,
            )
        raise AppError("REST_ERROR", "REST 生成任务超时", status_code=504, retryable=True)

    async def _download(self, client: httpx.AsyncClient, item: dict) -> bytes:
        url = item.get("download_url") or item.get("remote_url")
        if not url:
            raise AppError("REST_ERROR", "结果缺少下载地址", status_code=502)
        try:
            response = await asyncio.wait_for(
                client.get(url, timeout=300.0, follow_redirects=True),
                timeout=330.0,
            )
            if response.status_code >= 400:
                response = await asyncio.wait_for(
                    client.get(
                        url,
                        headers={"Authorization": self.headers["Authorization"]},
                        timeout=300.0,
                        follow_redirects=True,
                    ),
                    timeout=330.0,
                )
            response.raise_for_status()
            return response.content
        except asyncio.TimeoutError as exc:
            raise AppError("REST_ERROR", "下载生成结果超时", status_code=504, retryable=True) from exc
        except httpx.HTTPError as exc:
            raise AppError("REST_ERROR", f"下载结果失败：{exc}", status_code=502, retryable=True) from exc

    async def ping(self) -> dict:
        async with httpx.AsyncClient(timeout=httpx.Timeout(45.0, connect=15.0)) as client:
            data = await self._post(
                client,
                "/v1/capabilities/generation/generation_list",
                {"kind": "video", "limit": 1},
                timeout=45,
            )
            return {"ok": True, "total": data.get("total", 0)}

    async def generate_image(
        self,
        prompt: str,
        image_paths: list[Path] | None = None,
        *,
        params: dict | None = None,
    ) -> tuple[bytes, dict]:
        images = [self._data_url(path) for path in (image_paths or []) if path and path.exists()]
        args: dict[str, Any] = {
            "prompt": prompt,
            "params": params if params is not None else self.settings.rest_image_params_dict,
            "images": images,
            "wait_seconds": self.settings.rest_image_wait_seconds,
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=20.0), follow_redirects=True) as client:
            job = await self._post(
                client,
                "/v1/capabilities/generation/image_generate",
                args,
                timeout=180,
            )
            job = await self._wait(client, job)
            results = self._ready_results(job)
            if not results:
                raise AppError("REST_ERROR", "图片任务成功但没有结果", status_code=502)
            data = await self._download(client, results[0])
            return data, job

    async def generate_video(
        self,
        prompt: str,
        image_paths: list[Path],
        out_path: Path,
        *,
        seconds: float,
        ratio: str,
    ) -> dict:
        duration = max(4, int(round(seconds)))
        params = {
            "aspectRatio": self._aspect(ratio),
            "resolution": self.settings.rest_video_resolution,
            "duration": duration,
        }
        images = [self._data_url(path) for path in image_paths if path and path.exists()]
        args = {
            "prompt": prompt,
            "params": params,
            "images": images,
            "wait_seconds": self.settings.rest_video_wait_seconds,
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=20.0), follow_redirects=True) as client:
            job = await self._post(
                client,
                "/v1/capabilities/generation/video_generate",
                args,
                timeout=180,
            )
            job = await self._wait(client, job)
            results = self._ready_results(job)
            if not results:
                raise AppError("REST_ERROR", "视频任务成功但没有结果", status_code=502)
            data = await self._download(client, results[0])
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(data)
            return {
                "job_id": str(job.get("job_id")),
                "status": job.get("status"),
                "model": job.get("model"),
                "params": params,
                "url": results[0].get("remote_url") or results[0].get("download_url"),
                "size": len(data),
            }
