from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import Project, Skill
from ..utils import json_loads


def _category_match(skill_config: dict, category: str) -> float:
    suitable = skill_config.get("suitable_categories") or []
    if category in suitable:
        return 1.0
    for item in suitable:
        if item and (item in category or category in item):
            return 0.8
    return 0.25


def _goal_match(skill_config: dict, goal: str) -> float:
    return 1.0 if skill_config.get("goal") == goal else 0.55


def _platform_match(skill_config: dict, platform: str, duration: int) -> float:
    platforms = skill_config.get("platforms") or []
    durations = skill_config.get("durations") or []
    score = 1.0 if platform in platforms else 0.2
    if durations and duration in durations:
        score += 0.2
    return min(1.0, score)


def recommend_skills(
    session: Session,
    project: Project,
    brief: dict,
    *,
    goal: str = "转化",
    top_n: int = 3,
) -> list[dict]:
    rows = session.query(Skill).filter(Skill.enabled.is_(True)).all()
    scored: list[dict] = []
    for row in rows:
        config = json_loads(row.config_json, {})
        category_score = _category_match(config, brief.get("category", ""))
        goal_score = _goal_match(config, goal)
        platform_score = _platform_match(config, project.platform, project.duration)
        score = 0.4 * category_score + 0.3 * goal_score + 0.2 * platform_score + 0.1
        score = round(score, 4)

        reasons = []
        if category_score >= 0.8:
            reasons.append(f"适合「{brief.get('category', '该品类')}」")
        if goal_score >= 1.0:
            reasons.append(f"{goal}导向")
        if platform_score >= 0.8:
            reasons.append(f"适配 {project.platform} / {project.duration}s")
        if not reasons:
            reasons.append("通用结构，可作为备选")

        scored.append(
            {
                "id": row.id,
                "name": row.name,
                "category": row.category,
                "goal": row.goal,
                "version": row.version,
                "score": score,
                "reason": "，".join(reasons),
                "beats": len(config.get("beats") or []),
                "suitable_categories": config.get("suitable_categories") or [],
                "config": config,
            }
        )

    scored.sort(key=lambda item: item["score"], reverse=True)
    result = scored[:top_n]
    if not result:
        raise RuntimeError("没有可用的视频风格 Skill")
    return result
