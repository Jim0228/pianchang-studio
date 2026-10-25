from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import api
from .config import BASE_DIR, Settings
from .db import SessionLocal, init_db
from .errors import AppError
from .services import ffmpeg as ffmpeg_service
from .services.skill_loader import seed_skills

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


class SPAStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404:
                return await super().get_response("index.html", scope)
            raise
        if response.status_code == 404:
            return await super().get_response("index.html", scope)
        return response


def create_app(settings_override: Settings | None = None) -> FastAPI:
    settings = settings_override or Settings()
    settings.data_path.mkdir(parents=True, exist_ok=True)

    init_db(settings.sqlalchemy_url)
    session = SessionLocal()
    try:
        seed_skills(session)
    finally:
        session.close()

    app = FastAPI(title=settings.app_name, version=settings.version)
    app.state.settings = settings
    try:
        app.state.ffmpeg_status = ffmpeg_service.status(settings)
    except Exception as exc:  # noqa: BLE001
        app.state.ffmpeg_status = {"available": False, "error": str(exc)}
    logger.info("ffmpeg status: %s", app.state.ffmpeg_status)

    origins = [origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins or ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(AppError)
    async def app_error_handler(_request: Request, exc: AppError):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.to_dict()})

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(_request: Request, exc: StarletteHTTPException):
        detail = exc.detail
        if isinstance(detail, dict):
            return JSONResponse(status_code=exc.status_code, content={"detail": detail})
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": {"code": "HTTP_ERROR", "message": str(detail), "retryable": False}},
        )

    app.include_router(api.router, prefix="/api")
    app.mount("/media", StaticFiles(directory=str(settings.data_path)), name="media")

    dist = BASE_DIR / "web" / "dist"
    if dist.exists():
        app.mount("/", SPAStaticFiles(directory=str(dist), html=True), name="web")
    else:

        @app.get("/")
        def root():
            return {
                "app": settings.app_name,
                "message": "前端尚未构建，请先执行 pnpm build；API 文档见 /docs",
            }

    return app


app = create_app()
