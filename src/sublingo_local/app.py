from __future__ import annotations

import importlib.util
import os
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, File, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .jobs import JobManager, ProcessingPipeline
from .media import SUPPORTED_VIDEO_EXTENSIONS
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
    configured = os.getenv("SUBLINGO_DATA_DIR")
    return Path(configured).expanduser() if configured else Path.cwd() / "data"


def create_app(
    *,
    data_dir: Path | None = None,
    pipeline: ProcessingPipeline | None = None,
    static_dir: Path | None = None,
) -> FastAPI:
    resolved_data_dir = (data_dir or default_data_dir()).resolve()
    upload_store = UploadStore(resolved_data_dir / "uploads")
    actual_pipeline = pipeline or ProcessingPipeline(resolved_data_dir / "tmp")
    manager = JobManager(upload_store, actual_pipeline)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.data_dir = resolved_data_dir
        app.state.upload_store = upload_store
        app.state.job_manager = manager
        yield
        await manager.shutdown()

    app = FastAPI(
        title="SubLingo Local API",
        version=__version__,
        description="本地优先的视频转写与中文字幕服务",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[],
        allow_origin_regex=r"https?://(127\.0\.0\.1|localhost)(:\d+)?",
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    api = APIRouter(prefix="/api")

    async def health_handler() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "sublingo-local",
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
                "models": ["large-v3", "large-v3-turbo", "medium", "small"],
                "languages": ["auto", "ja", "en"],
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
                "ffmpeg": shutil.which("ffmpeg") is not None,
                "codex": shutil.which("codex") is not None,
                "nvidia_smi": shutil.which("nvidia-smi") is not None,
                "system_file_picker": os.name == "nt",
            },
            "video_extensions": sorted(SUPPORTED_VIDEO_EXTENSIONS),
        }

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

    # Minimal unprefixed compatibility endpoints for scripts and curl users.
    app.add_api_route("/health", health_handler, methods=["GET"], include_in_schema=False)
    app.add_api_route(
        "/capabilities", capabilities_handler, methods=["GET"], include_in_schema=False
    )
    app.add_api_route("/uploads", upload_handler, methods=["POST"], include_in_schema=False)
    app.add_api_route("/jobs", create_job_handler, methods=["POST"], include_in_schema=False)
    app.add_api_route("/jobs/{job_id}", get_job_handler, methods=["GET"], include_in_schema=False)
    app.add_api_route("/pick-video", pick_video_handler, methods=["POST"], include_in_schema=False)
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
