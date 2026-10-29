from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.config import Settings  # noqa: E402
from backend.app.main import create_app  # noqa: E402


@pytest.fixture()
def client(tmp_path: Path):
    settings = Settings(
        data_dir=str(tmp_path / "data"),
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        sync_jobs=True,
        ai_mode="mock",
        llm_provider="mock",
        image_provider="mock",
        video_provider="mock",
        video_width=540,
        video_height=960,
        fps=24,
        tts_enabled=False,
        bgm_enabled=False,
    )
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client
