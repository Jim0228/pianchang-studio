from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx

from ...config import Settings
from ...errors import AppError
from ...utils import parse_json_loose


def _mime(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".png"}:
        return "image/png"
    if suffix in {".webp"}:
        return "image/webp"
    return "image/jpeg"


def _data_url(path: Path) -> str:
    payload = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{_mime(path)};base64,{payload}"


class ArkClient:
    def __init__(self, settings: Settings):
        if not settings.ark_key_present:
            raise AppError("ARK_KEY_MISSING", "real 模式需要配置 ARK_API_KEY", status_code=400)
        self.settings = settings
        self.base_url = settings.ark_base_url.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {settings.ark_api_key}",
            "Content-Type": "application/json",
        }

    async def _post(self, path: str, payload: dict, *, timeout: float = 180.0) -> dict:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=20.0)) as client:
                response = await client.post(f"{self.base_url}{path}", headers=self.headers, json=payload)
        except httpx.HTTPError as exc:
            raise AppError("PROVIDER_ERROR", f"方舟请求失败：{exc}", status_code=502, retryable=True) from exc
        if response.status_code >= 400:
            body = response.text[:500]
            raise AppError(
                "PROVIDER_ERROR",
                f"方舟返回 {response.status_code}：{body}",
                status_code=502,
                retryable=response.status_code in {429, 500, 502, 503, 504},
            )
        return response.json()

    async def chat_json(self, messages: list[dict], *, model: str, temperature: float = 0.7) -> dict:
        payload = {"model": model, "messages": messages, "temperature": temperature}
        try:
            data = await self._post("/chat/completions", {**payload, "response_format": {"type": "json_object"}})
        except AppError:
            data = await self._post("/chat/completions", payload)
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AppError("PROVIDER_ERROR", "方舟返回内容为空", status_code=502) from exc
        if isinstance(content, list):
            content = "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
        parsed = parse_json_loose(str(content))
        if not parsed:
            raise AppError("PROVIDER_ERROR", "方舟未返回合法 JSON", status_code=502, retryable=True)
        return parsed

    async def vision_brief(self, image_paths: list[Path], description: str, name: str) -> dict:
        system = (
            "你是资深电商商品分析助手。根据用户提供的商品图与描述，输出严格的 JSON，不要 Markdown。"
            "字段：title, category, selling_points(数组，3-5 条，每条<=30字), audience, price, tone, "
            "pain_point, keywords(数组), risks(数组)。不得虚构参数、功效、认证或销量。"
        )
        content: list[dict] = [
            {
                "type": "text",
                "text": f"商品名：{name or '未命名'}\n商品描述：{description or '无'}\n请分析并输出 JSON。",
            }
        ]
        for path in image_paths[:3]:
            if path.exists():
                content.append({"type": "image_url", "image_url": {"url": _data_url(path)}})
        messages = [{"role": "system", "content": system}, {"role": "user", "content": content}]
        return await self.chat_json(messages, model=self.settings.ark_vision_model, temperature=0.2)

    async def write_script(
        self,
        brief: dict,
        skill_config: dict,
        platform: str,
        duration: int,
        ratio: str,
        goal: str,
    ) -> dict:
        system = (
            "你是资深电商短视频编导。根据商品档案与视频风格 Skill，输出严格 JSON，不要 Markdown。"
            "结构：{title, shots:[{idx,seconds,beat,scene,camera,action,voiceover,on_screen,prompt}], "
            "cover:{title,subtitle}, caption:{title,body,hashtags:[...]}, style_bible:{...}}。"
            f"镜头数 4-6，总时长必须等于 {duration} 秒；on_screen<=18字；voiceover<=40字。"
            "不得虚构参数、功效、销量、认证；不得使用绝对化用语。"
        )
        user = json.dumps(
            {
                "brief": brief,
                "skill": {
                    "id": skill_config.get("id"),
                    "name": skill_config.get("name"),
                    "beats": skill_config.get("beats"),
                    "scenes": skill_config.get("scenes"),
                    "prompt": skill_config.get("prompt"),
                    "subtitle": skill_config.get("subtitle"),
                },
                "platform": platform,
                "duration": duration,
                "ratio": ratio,
                "goal": goal,
            },
            ensure_ascii=False,
        )
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        return await self.chat_json(messages, model=self.settings.ark_chat_model)

    async def generate_image(self, prompt: str, reference_paths: list[Path] | None = None) -> bytes:
        payload: dict = {
            "model": self.settings.ark_image_model,
            "prompt": prompt,
            "size": self.settings.ark_image_size,
            "response_format": "url",
            "watermark": False,
        }
        if reference_paths:
            payload["image"] = [_data_url(path) for path in reference_paths if path.exists()]
        data = await self._post("/images/generations", payload, timeout=300.0)
        try:
            item = data["data"][0]
        except (KeyError, IndexError, TypeError) as exc:
            raise AppError("PROVIDER_ERROR", "方舟未返回图片", status_code=502) from exc
        if item.get("b64_json"):
            return base64.b64decode(item["b64_json"])
        url = item.get("url")
        if not url:
            raise AppError("PROVIDER_ERROR", "方舟图片缺少 url", status_code=502)
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.get(url)
                response.raise_for_status()
                return response.content
        except httpx.HTTPError as exc:
            raise AppError("PROVIDER_ERROR", f"下载生成图片失败：{exc}", status_code=502) from exc
