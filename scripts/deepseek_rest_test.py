from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.config import Settings
from backend.app.db import SessionLocal, init_db
from backend.app.jobs import _rest_clip_one
from backend.app.models import Asset, Product, Project, Script, Shot, Skill
from backend.app.services.brief import analyze_product
from backend.app.services.ffmpeg import probe
from backend.app.services.script import generate_script
from backend.app.services.skill_loader import seed_skills


async def run(phase: str) -> int:
    settings = Settings()
    print("providers", settings.llm_provider_name, settings.image_provider_name, settings.video_provider_name)
    init_db(settings.sqlalchemy_url)
    session = SessionLocal()
    try:
        seed_skills(session)
        project = session.query(Project).filter(Project.name == "厨房油污清洁剂").first()
        if project is None:
            project = session.query(Project).order_by(Project.created_at.desc()).first()
        skill = session.get(Skill, "pain_point_reversal")
        product = await analyze_product(session, project, settings)
        brief = json.loads(product.brief_json)
        print("brief", brief.get("source"), brief.get("category"), brief.get("selling_points"), brief.get("price"))
        script = await generate_script(
            session,
            project,
            product,
            skill,
            "douyin",
            15,
            "9:16",
            settings,
        )
        data = json.loads(script.script_json)
        print("script", script.id, data.get("source"), len(data.get("shots", [])), sum(float(s["seconds"]) for s in data["shots"]))
        print("title", data.get("title"))
        print("shots", [(s.get("beat"), s.get("on_screen"), s.get("voiceover")) for s in data["shots"]])
        if phase != "shot":
            return 0
        shot = session.query(Shot).filter(Shot.script_id == script.id, Shot.idx == 1).first()
        project_id, script_id, shot_id = project.id, script.id, shot.id
    finally:
        session.close()

    if phase == "shot":
        await _rest_clip_one(project_id, script_id, shot_id, settings)
        session = SessionLocal()
        try:
            shot = session.get(Shot, shot_id)
            asset = session.get(Asset, shot.clip_asset_id) if shot.clip_asset_id else None
            print("shot", shot.status, shot.error[:300])
            if asset:
                print("asset", asset.path, asset.width, asset.height, asset.duration)
                print("meta", json.dumps(json.loads(asset.meta_json), ensure_ascii=False)[:800])
                print("probe", probe(settings, settings.data_path / asset.path))
        finally:
            session.close()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["llm", "shot"], default="llm", nargs="?")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.phase)))
