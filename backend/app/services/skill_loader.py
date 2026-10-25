from __future__ import annotations

from pathlib import Path

import yaml
from sqlalchemy.orm import Session

from ..models import Skill
from ..utils import json_dumps

SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"


def load_skill_configs() -> list[dict]:
    configs: list[dict] = []
    for path in sorted(SKILLS_DIR.glob("*.yaml")):
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        if not data.get("id"):
            continue
        data.setdefault("version", 1)
        data.setdefault("beats", [])
        data.setdefault("scenes", [])
        data.setdefault("prompt", {})
        data.setdefault("shots", {})
        data.setdefault("subtitle", {})
        data.setdefault("audio", {})
        configs.append(data)
    return configs


def seed_skills(db: Session) -> None:
    for config in load_skill_configs():
        row = db.get(Skill, config["id"])
        if row is None:
            row = Skill(
                id=config["id"],
                name=config.get("name", config["id"]),
                category=config.get("category", "conversion"),
                goal=config.get("goal", "转化"),
                version=int(config.get("version", 1)),
                config_json=json_dumps(config),
                enabled=True,
            )
            db.add(row)
        else:
            row.name = config.get("name", row.name)
            row.category = config.get("category", row.category)
            row.goal = config.get("goal", row.goal)
            row.version = int(config.get("version", row.version))
            row.config_json = json_dumps(config)
            row.enabled = True
    db.commit()
