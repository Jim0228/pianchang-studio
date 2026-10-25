from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from urllib.parse import urlparse

import httpx

from ...config import Settings
from ...errors import AppError


class McpClient:
    """Minimal MCP Streamable HTTP client for the gateway's video tools."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.url = settings.mcp_video_url.strip()
        self.token = settings.mcp_video_token.strip()
        if not self.url or not self.token:
            raise AppError(
                "MCP_NOT_CONFIGURED",
                "MCP 视频生成未配置：请设置 MCP_VIDEO_URL 与 MCP_VIDEO_TOKEN",
                status_code=400,
            )
        self.session_id: str | None = None
        self._request_id = 0
        self._origin = self._origin_of(self.url)

    @staticmethod
    def _origin_of(url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": self.token if self.token.lower().startswith("bearer ") else f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-06-18",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    @staticmethod
    def _parse(response: httpx.Response) -> dict:
        if "text/event-stream" in response.headers.get("content-type", ""):
            last = None
            for line in response.text.splitlines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    last = json.loads(payload)
                except json.JSONDecodeError:
                    continue
            return last or {}
        try:
            return response.json()
        except Exception:
            return {"raw": response.text[:1000]}

    async def _post(self, client: httpx.AsyncClient, payload: dict) -> tuple[httpx.Response, dict]:
        try:
            response = await client.post(self.url, headers=self._headers(), json=payload)
        except httpx.HTTPError as exc:
            raise AppError("MCP_ERROR", f"MCP 请求失败：{exc}", status_code=502, retryable=True) from exc
        if response.status_code >= 400:
            raise AppError(
                "MCP_ERROR",
                f"MCP 返回 {response.status_code}：{response.text[:400]}",
                status_code=502,
                retryable=response.status_code in {429, 500, 502, 503, 504},
            )
        session_id = response.headers.get("mcp-session-id")
        if session_id:
            self.session_id = session_id
        data = self._parse(response)
        if isinstance(data, dict) and data.get("error"):
            error = data["error"]
            message = error.get("message") if isinstance(error, dict) else str(error)
            raise AppError("MCP_ERROR", f"MCP 错误：{message}", status_code=502)
        return response, data

    async def initialize(self, client: httpx.AsyncClient) -> dict:
        self._request_id += 1
        _, data = await self._post(
            client,
            {
                "jsonrpc": "2.0",
                "id": self._request_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "ecom-video-studio", "version": "0.1.0"},
                },
            },
        )
        await self._post(client, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        return data.get("result") or data

    async def _tools_call(self, client: httpx.AsyncClient, name: str, arguments: dict) -> dict:
        self._request_id += 1
        _, data = await self._post(
            client,
            {
                "jsonrpc": "2.0",
                "id": self._request_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
        )
        result = data.get("result") if isinstance(data, dict) else None
        if not isinstance(result, dict):
            raise AppError("MCP_ERROR", "MCP 工具返回为空", status_code=502, retryable=True)
        if result.get("isError"):
            raise AppError("MCP_ERROR", self._text_of(result) or "MCP 工具执行失败", status_code=502)
        return self._payload(result)

    @staticmethod
    def _text_of(result: dict) -> str:
        for item in result.get("content") or []:
            if isinstance(item, dict) and item.get("type") == "text":
                return str(item.get("text", ""))
        return ""

    def _payload(self, result: dict) -> dict:
        text = self._text_of(result)
        if not text:
            return {}
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return {"raw": text}
        if isinstance(data, dict) and data.get("error"):
            error = data["error"]
            message = error.get("message") if isinstance(error, dict) else str(error)
            raise AppError("MCP_ERROR", f"MCP 任务失败：{message}", status_code=502)
        return data if isinstance(data, dict) else {"raw": text}

    def _data_url(self, path: Path) -> str:
        suffix = path.suffix.lower()
        mime = "image/png" if suffix == ".png" else "image/jpeg"
        payload = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{payload}"

    @staticmethod
    def _aspect(ratio: str) -> str:
        if ratio in {"9:16", "3:4"}:
            return "portrait"
        if ratio in {"16:9"}:
            return "landscape"
        return "square"

    async def ping(self) -> dict:
        async with httpx.AsyncClient(timeout=httpx.Timeout(45.0, connect=15.0), follow_redirects=True) as client:
            await self.initialize(client)
            self._request_id += 1
            _, data = await self._post(
                client,
                {"jsonrpc": "2.0", "id": self._request_id, "method": "tools/list", "params": {}},
            )
            tools = (data.get("result") or {}).get("tools") or []
            return {"ok": True, "tools": [tool.get("name") for tool in tools]}

    async def generate_video(
        self,
        prompt: str,
        image_paths: list[Path],
        out_path: Path,
        *,
        seconds: float,
        ratio: str,
    ) -> dict:
        images = [self._data_url(path) for path in image_paths if path and path.exists()]
        duration = max(4, int(round(seconds)))
        params = {
            "aspectRatio": self._aspect(ratio),
            "resolution": self.settings.mcp_video_resolution,
            "duration": duration,
        }
        deadline = asyncio.get_running_loop().time() + max(60, self.settings.mcp_video_timeout)
        poll_seconds = max(5, min(120, self.settings.mcp_video_poll_seconds))

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(150.0, connect=20.0),
            follow_redirects=True,
        ) as client:
            await self.initialize(client)
            payload = await self._tools_call(
                client,
                self.settings.mcp_video_tool,
                {
                    "prompt": prompt,
                    "params": params,
                    "images": images,
                    "wait_seconds": 5,
                },
            )
            job_id = payload.get("job_id") or payload.get("id")
            if not job_id:
                raise AppError("MCP_ERROR", "MCP 未返回任务 ID", status_code=502)

            data = payload
            while asyncio.get_running_loop().time() < deadline:
                status = str(data.get("status", "")).lower()
                if status in {"failed", "error", "cancelled"}:
                    raise AppError("MCP_ERROR", f"MCP 视频任务失败：{data.get('error') or status}", status_code=502)
                results = data.get("results") or []
                ready = [
                    item
                    for item in results
                    if item.get("status") == "ready"
                    and (item.get("remote_url") or item.get("download_url") or item.get("url"))
                ]
                if status in {"succeeded", "success", "completed"} and ready:
                    url = ready[0].get("remote_url") or ready[0].get("download_url") or ready[0].get("url")
                    await self._download(client, str(url), out_path)
                    return {
                        "job_id": str(job_id),
                        "status": status,
                        "url": str(url),
                        "params": params,
                        "duration": duration,
                    }
                if status in {"succeeded", "success", "completed"}:
                    await asyncio.sleep(2)
                    data = await self._tools_call(client, "generation_result", {"job_id": str(job_id)})
                    continue
                data = await self._tools_call(
                    client,
                    "generation_job",
                    {"job_id": str(job_id), "wait_seconds": poll_seconds},
                )
            raise AppError("MCP_ERROR", "MCP 视频任务超时", status_code=504, retryable=True)

    async def _download(self, client: httpx.AsyncClient, url: str, out_path: Path) -> None:
        if url.startswith("/"):
            url = f"{self._origin}{url}"
        headers = {}
        if urlparse(url).netloc == urlparse(self.url).netloc:
            headers["Authorization"] = self._headers()["Authorization"]
        out_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            async with client.stream("GET", url, headers=headers, timeout=300.0) as response:
                response.raise_for_status()
                with out_path.open("wb") as handle:
                    async for chunk in response.aiter_bytes():
                        handle.write(chunk)
        except httpx.HTTPError as exc:
            raise AppError("MCP_ERROR", f"下载 MCP 视频失败：{exc}", status_code=502, retryable=True) from exc
        if not out_path.exists() or out_path.stat().st_size == 0:
            raise AppError("MCP_ERROR", "MCP 视频下载为空", status_code=502, retryable=True)
