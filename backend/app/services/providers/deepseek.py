from __future__ import annotations

import json

import httpx

from ...config import Settings
from ...errors import AppError
from ...utils import parse_json_loose


class DeepSeekClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.key = settings.deepseek_api_key.strip()
        if not self.key:
            raise AppError("DEEPSEEK_KEY_MISSING", "DeepSeek 未配置：请设置 DEEPSEEK_API_KEY", status_code=400)
        self.base = settings.deepseek_base_url.strip().rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
        }

    async def _chat(self, messages: list[dict], *, temperature: float = 0.7, max_tokens: int = 4096) -> str:
        payload = {
            "model": self.settings.deepseek_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=20.0)) as client:
                response = await client.post(
                    f"{self.base}/chat/completions",
                    headers=self.headers,
                    json=payload,
                )
        except httpx.HTTPError as exc:
            raise AppError("PROVIDER_ERROR", f"DeepSeek 请求失败：{exc}", status_code=502, retryable=True) from exc
        if response.status_code >= 400:
            raise AppError(
                "PROVIDER_ERROR",
                f"DeepSeek 返回 {response.status_code}：{response.text[:500]}",
                status_code=502,
                retryable=response.status_code in {429, 500, 502, 503, 504},
            )
        data = response.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AppError("PROVIDER_ERROR", "DeepSeek 返回内容为空", status_code=502) from exc
        return str(content or "")

    async def _chat_json(self, messages: list[dict], *, temperature: float = 0.7, max_tokens: int = 4096) -> dict:
        content = await self._chat(messages, temperature=temperature, max_tokens=max_tokens)
        parsed = parse_json_loose(content)
        if not parsed:
            raise AppError("PROVIDER_ERROR", "DeepSeek 未返回合法 JSON", status_code=502, retryable=True)
        return parsed

    async def analyze(self, description: str, name: str) -> dict:
        system = (
            "你是资深电商商品分析助手。根据商品名与描述输出严格 JSON，不要 Markdown。"
            "字段：title, category, selling_points(数组，3-5 条，每条<=30字), audience, price, tone, "
            "pain_point, keywords(数组), risks(数组)。不得虚构参数、功效、认证或销量。"
        )
        user = f"商品名：{name or '未命名'}\n商品描述：{description or '无'}\n请输出 JSON。"
        return await self._chat_json(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.2,
            max_tokens=1500,
        )

    async def write_script(
        self,
        brief: dict,
        skill_config: dict,
        platform: str,
        duration: int,
        ratio: str,
        goal: str,
    ) -> dict:
        presenter = skill_config.get("presenter") or {}
        continuity = skill_config.get("continuity") or {}
        voice = skill_config.get("voice") or {}
        people_rule = (
            "画面必须出现同一位人物："
            + str(presenter.get("description") or "")
            + "；全片同一人、同一套衣服、同一发型，动作自然。"
            if presenter.get("enabled")
            else "画面不出现人物与手部。"
        )
        arc = " → ".join(str(item) for item in (continuity.get("action_arc") or []))
        continuity_rule = (
            f"动作必须连贯：每镜的起始动作承接上一镜的结束动作，动作弧线为 {arc}；"
            "场景、台面、固定道具位置不变，只改变景别、机位与动作阶段。"
            if arc
            else "每个镜头是独立的商品镜头。"
        )
        voice_budget = max(8, int(duration * 2.9))
        line_style = str(voice.get("line_style") or "口语短句")
        system = (
            "你是资深电商短视频编导。根据商品档案与视频风格 Skill，输出严格 JSON，不要 Markdown。"
            "结构：{title, shots:[{idx,seconds,beat,scene,camera,action,voiceover,on_screen,prompt}], "
            "cover:{title,subtitle}, caption:{title,body,hashtags:[...]}, style_bible:{...}}。"
            f"镜头数 4-6，总时长必须等于 {duration} 秒；on_screen<=18字。"
            f"voiceover 是给 TTS 朗读的台词：全片总字数 <= {voice_budget} 字，"
            "单镜字数 <= 该镜秒数×3.2，必须是能直接朗读的中文短句，不要书面长句、不要括号注释、不要 emoji；"
            f"风格：{line_style}。"
            "scene/action 只写画面：商品 + 场景/道具 + 光线/运镜，"
            "全片同一款同一颜色商品，每个画面只出现一件商品；所有镜头限定在同一场景空间。"
            f"{people_rule}{continuity_rule}"
            "不写文字、价格牌、认证标志、销量、第二名商品；不得虚构参数、功效、销量、认证；"
            "不得使用绝对化用语。只输出 JSON。"
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
                    "presenter": presenter,
                    "continuity": continuity,
                    "voice": voice,
                    "audio": skill_config.get("audio"),
                },
                "platform": platform,
                "duration": duration,
                "ratio": ratio,
                "goal": goal,
                "voice_budget_chars": voice_budget,
            },
            ensure_ascii=False,
        )
        return await self._chat_json(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.7,
            max_tokens=4096,
        )
