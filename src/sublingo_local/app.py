from __future__ import annotations

import asyncio
import importlib.util
import os
import secrets
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, File, HTTPException, Request, UploadFile, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .environment import EnvironmentService, EnvironmentView
from .jobs import JobManager, ProcessingPipeline
from .media import SUPPORTED_VIDEO_EXTENSIONS
from .model_manager import ModelListView, ModelManager, ModelView
from .models import (
    JobCreateRequest,
    JobView,
    OpenFolderRequest,
    OpenFolderResult,
    PickVideoResult,
    UploadView,
)
from .storage import UploadStore
from .system import SystemIntegrationUnavailable, open_folder, pick_video

UPLOAD_FILE = File(...)


def default_data_dir() -> Path:
    configured = os.getenv("CAPTIONNEST_DATA_DIR")
    return Path(configured).expanduser() if configured else Path.cwd() / "data"


def create_app(
    *,
    data_dir: Path | None = None,
    pipeline: ProcessingPipeline | None = None,
    static_dir: Path | None = None,
) -> FastAPI:
    resolved_data_dir = (data_dir or default_data_dir()).resolve()
    upload_store = UploadStore(resolved_data_dir / "uploads")
    model_manager = ModelManager(resolved_data_dir / "models")
    environment_service = EnvironmentService(model_manager)
    actual_pipeline = pipeline or ProcessingPipeline(
        resolved_data_dir / "tmp", model_manager=model_manager
    )
    manager = JobManager(upload_store, actual_pipeline)
    session_token = os.getenv("CAPTIONNEST_SESSION_TOKEN", "").strip()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.data_dir = resolved_data_dir
        app.state.upload_store = upload_store
        app.state.job_manager = manager
        app.state.model_manager = model_manager
        app.state.environment_service = environment_service
        yield
        await manager.shutdown()

    app = FastAPI(
        title="CaptionNest API",
        version=__version__,
        description="本地优先的视频转写与中文字幕服务",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[],
        allow_origin_regex=r"https?://(tauri\.localhost|127\.0\.0\.1|localhost)(:\d+)?",
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def require_desktop_session(request: Request, call_next):  # type: ignore[no-untyped-def]
        if (
            session_token
            and request.method != "OPTIONS"
            and request.url.path.startswith("/api/")
        ):
            supplied = request.headers.get("X-CaptionNest-Session", "")
            if not supplied or not secrets.compare_digest(supplied, session_token):
                return JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={"detail": "桌面会话无效"},
                )
        return await call_next(request)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        del request
        # FastAPI includes the rejected request value in `input` by default. A failed
        # model-level validation can therefore echo an API key; only return safe fields.
        errors = [
            {
                "type": error.get("type", "value_error"),
                "loc": error.get("loc", ()),
                "msg": error.get("msg", "请求参数无效"),
            }
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"detail": errors},
        )

    api = APIRouter(prefix="/api")

    async def health_handler() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "captionnest",
            "version": __version__,
            "data_dir": str(resolved_data_dir),
        }

    async def capabilities_handler() -> dict[str, Any]:
        asr_installed = importlib.util.find_spec("faster_whisper") is not None
        cuda_available = False
        if asr_installed:
            try:
                ctranslate2 = importlib.import_module("ctranslate2")
                cuda_available = ctranslate2.get_cuda_device_count() > 0
            except (AttributeError, ImportError, OSError, RuntimeError):
                cuda_available = False
        return {
            "asr": {
                "provider": "faster-whisper",
                "installed": asr_installed,
                "cuda_available": cuda_available,
                "models": ["small", "medium", "large-v3-turbo", "large-v3"],
                "source_language": "auto",
                "target_languages": ["zh-CN", "en", "ko"],
            },
            "translation": {
                "providers": [
                    {
                        "id": "codex_spark",
                        "default_model": "gpt-5.3-codex-spark",
                        "key_required": False,
                    },
                    {
                        "id": "lmstudio",
                        "default_endpoint": "http://127.0.0.1:1234/v1",
                        "key_required": False,
                    },
                    {
                        "id": "deepseek",
                        "default_model": "deepseek-v4-flash",
                        "default_endpoint": "https://api.deepseek.com",
                        "key_required": True,
                    },
                ]
            },
            "tools": {
                "media_decoder": importlib.util.find_spec("av") is not None,
                "codex": shutil.which("codex") is not None,
                "nvidia_smi": shutil.which("nvidia-smi") is not None,
                "system_file_picker": os.name == "nt",
            },
            "video_extensions": sorted(SUPPORTED_VIDEO_EXTENSIONS),
        }

    async def environment_handler() -> EnvironmentView:
        return await asyncio.to_thread(environment_service.check)

    async def models_handler() -> ModelListView:
        return model_manager.list()

    async def download_model_handler(model_id: str) -> ModelView:
        try:
            return model_manager.start_download(model_id)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    async def upload_handler(file: UploadFile = UPLOAD_FILE) -> UploadView:
        try:
            return await upload_store.save(file)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    async def create_job_handler(request: JobCreateRequest) -> JobView:
        try:
            return manager.create(request)
        except (ValueError, KeyError, OSError) as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    async def list_jobs_handler() -> list[JobView]:
        return manager.list()

    async def get_job_handler(job_id: str) -> JobView:
        try:
            return manager.get(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    async def pick_video_handler() -> PickVideoResult:
        try:
            return await pick_video()
        except SystemIntegrationUnavailable as exc:
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)
            ) from exc

    async def open_folder_handler(request: OpenFolderRequest) -> OpenFolderResult:
        try:
            return open_folder(request.path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="路径不存在") from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except SystemIntegrationUnavailable as exc:
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)
            ) from exc

    api.add_api_route("/health", health_handler, methods=["GET"], tags=["system"])
    api.add_api_route("/capabilities", capabilities_handler, methods=["GET"], tags=["system"])
    api.add_api_route(
        "/environment",
        environment_handler,
        methods=["GET"],
        response_model=EnvironmentView,
        tags=["system"],
    )
    api.add_api_route(
        "/models",
        models_handler,
        methods=["GET"],
        response_model=ModelListView,
        tags=["models"],
    )
    api.add_api_route(
        "/models/{model_id}/download",
        download_model_handler,
        methods=["POST"],
        response_model=ModelView,
        tags=["models"],
    )
    api.add_api_route(
        "/uploads", upload_handler, methods=["POST"], response_model=UploadView, tags=["uploads"]
    )
    api.add_api_route(
        "/jobs", create_job_handler, methods=["POST"], response_model=JobView, tags=["jobs"]
    )
    api.add_api_route(
        "/jobs", list_jobs_handler, methods=["GET"], response_model=list[JobView], tags=["jobs"]
    )
    api.add_api_route(
        "/jobs/{job_id}", get_job_handler, methods=["GET"], response_model=JobView, tags=["jobs"]
    )
    api.add_api_route(
        "/system/pick-video",
        pick_video_handler,
        methods=["POST"],
        response_model=PickVideoResult,
        tags=["system"],
    )
    api.add_api_route(
        "/pick-video",
        pick_video_handler,
        methods=["POST"],
        response_model=PickVideoResult,
        include_in_schema=False,
    )
    api.add_api_route(
        "/system/open-folder",
        open_folder_handler,
        methods=["POST"],
        response_model=OpenFolderResult,
        tags=["system"],
    )
    api.add_api_route(
        "/open-folder",
        open_folder_handler,
        methods=["POST"],
        response_model=OpenFolderResult,
        include_in_schema=False,
    )
    app.include_router(api)

    # Development scripts can use the historic unprefixed routes. The packaged desktop
    # process enables a session token, so those bypass routes are intentionally absent there.
    if not session_token:
        app.add_api_route("/health", health_handler, methods=["GET"], include_in_schema=False)
        app.add_api_route(
            "/capabilities", capabilities_handler, methods=["GET"], include_in_schema=False
        )
        app.add_api_route("/uploads", upload_handler, methods=["POST"], include_in_schema=False)
        app.add_api_route("/jobs", create_job_handler, methods=["POST"], include_in_schema=False)
        app.add_api_route(
            "/jobs/{job_id}", get_job_handler, methods=["GET"], include_in_schema=False
        )
        app.add_api_route(
            "/pick-video", pick_video_handler, methods=["POST"], include_in_schema=False
        )
        app.add_api_route(
            "/open-folder", open_folder_handler, methods=["POST"], include_in_schema=False
        )

    project_root = Path(__file__).resolve().parents[2]
    dist = (static_dir or project_root / "web" / "dist").resolve()
    index = dist / "index.html"
    if index.is_file():
        assets = dist / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str) -> FileResponse:
            candidate = (dist / full_path).resolve()
            if candidate.is_file() and (candidate == dist or dist in candidate.parents):
                return FileResponse(candidate)
            return FileResponse(index)

    return app


app = create_app()
