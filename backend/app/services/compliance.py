from __future__ import annotations

import re
from typing import Any

RULES: list[dict[str, Any]] = [
    {"pattern": r"最(好|佳|优|强|低价|便宜|先进|新|高|低|大|小|多|少|快|慢|顶级)", "level": "warn", "message": "广告法风险：避免使用“最”类绝对化用语"},
    {"pattern": r"第一(品牌|名|选择|家)?", "level": "warn", "message": "广告法风险：避免“第一”类排他表述"},
    {"pattern": r"(国家级|世界级|顶级|绝对|唯一|独家|万能|永久)", "level": "warn", "message": "广告法风险：避免绝对化/排他性用语"},
    {"pattern": r"(100%|百分百)(有效|见效|治愈|安全|无添加)?", "level": "block", "message": "禁止承诺 100% 效果或安全"},
    {"pattern": r"(纯天然|无副作用|零添加)", "level": "warn", "message": "功效/成分宣称需有依据，谨慎使用"},
    {"pattern": r"(包治|根治|药到病除|立竿见影)", "level": "block", "message": "禁止医疗功效承诺"},
    {"pattern": r"(刷单|好评返现|虚假交易)", "level": "block", "message": "平台规则禁止交易作弊"},
    {"pattern": r"(全网最低|史上最低|最低价)", "level": "warn", "message": "价格比较需谨慎，避免无法证明的最低价"},
]

_COMPILED = [(re.compile(rule["pattern"]), rule) for rule in RULES]


def check_text(text: str, field: str) -> list[dict]:
    results: list[dict] = []
    if not text:
        return results
    for regex, rule in _COMPILED:
        for match in regex.finditer(text):
            results.append(
                {
                    "field": field,
                    "word": match.group(0),
                    "level": rule["level"],
                    "message": rule["message"],
                }
            )
    return results


def check_script(script: dict) -> list[dict]:
    results: list[dict] = []
    results.extend(check_text(script.get("title", ""), "title"))
    caption = script.get("caption", {}) or {}
    results.extend(check_text(caption.get("title", ""), "caption.title"))
    results.extend(check_text(caption.get("body", ""), "caption.body"))
    for shot in script.get("shots", []) or []:
        prefix = f"shots[{shot.get('idx', '?')}]"
        results.extend(check_text(shot.get("voiceover", ""), f"{prefix}.voiceover"))
        results.extend(check_text(shot.get("on_screen", ""), f"{prefix}.on_screen"))
    return results
