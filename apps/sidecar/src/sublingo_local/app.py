from __future__ import annotations

import asyncio
import importlib.util
import os
import secrets
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, FastAPI, File, HTTPException, Query, Request, UploadFile, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .batch_store import BatchStore
from .batches import BatchManager
from .environment import EnvironmentService, EnvironmentView
from .job_store import JobStore
from .jobs import JobManager, ProcessingPipeline
from .media import SUPPORTED_VIDEO_EXTENSIONS
from .model_manager import ModelListView, ModelManager, ModelView
from .models import (
    BatchCreateRequest,
    BatchCreateResult,
    BatchDeleteResult,
    BatchPreflightRequest,
    BatchPreflightResult,
    BatchRecord,
    BatchRunRequest,
    BulkUploadItemResult,
    BulkUploadResponse,
    JobBulkAction,
    JobBulkActionRequest,
    JobBulkActionResponse,
    JobCreateRequest,
    JobDeleteResult,
    JobRunRequest,
    JobStatus,
    JobStep,
    JobStepConfigUpdate,
    JobSummaryPage,
    JobView,
    OpenFolderRequest,
    OpenFolderResult,
    PickVideoResult,
    SchedulerSettings,
    UploadView,
)
from .storage import UploadStore
from .system import SystemIntegrationUnavailable, open_folder, pick_video

UPLOAD_FILE = File(...)
UPLOAD_FILES = File(...)


def default_data_dir() -> Path:
    configured = os.getenv("CAPTIONNEST_DATA_DIR")
    return Path(configured).expanduser() if configured else Path.cwd() / "data"


def create_app(
    *,
    data_dir: Path | None = None,
    pipeline: ProcessingPipeline | None = None,
    scheduler_settings: SchedulerSettings | None = None,
    static_dir: Path | None = None,
) -> FastAPI:
    resolved_data_dir = (data_dir or default_data_dir()).resolve()
    upload_store = UploadStore(resolved_data_dir / "uploads")
    job_store = JobStore(resolved_data_dir / "jobs")
    batch_store = BatchStore(resolved_data_dir / "batches")
    model_manager = ModelManager(resolved_data_dir / "models")
    environment_service = EnvironmentService(model_manager)
    actual_pipeline = pipeline or ProcessingPipeline(
        job_store.root,
        model_manager=model_manager,
        job_store=job_store,
    )
    manager = JobManager(
        upload_store,
        actual_pipeline,
        job_store=job_store,
        scheduler_settings=scheduler_settings,
    )
    batch_manager = BatchManager(batch_store, manager)
    session_token = os.getenv("CAPTIONNEST_SESSION_TOKEN", "").strip()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.data_dir = resolved_data_dir
        app.state.upload_store = upload_store
        app.state.job_store = job_store
        app.state.batch_store = batch_store
        app.state.batch_manager = batch_manager
        app.state.job_manager = manager
        app.state.model_manager = model_manager
        app.state.environment_service = environment_service
        manager.start()
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
        faster_whisper_installed = importlib.util.find_spec("faster_whisper") is not None
        faster_whisper_cuda = False
        if faster_whisper_installed:
            try:
                ctranslate2 = importlib.import_module("ctranslate2")
                faster_whisper_cuda = ctranslate2.get_cuda_device_count() > 0
            except (AttributeError, ImportError, OSError, RuntimeError):
                faster_whisper_cuda = False
        return {
            "asr": {
                "provider": "faster-whisper",
                "installed": faster_whisper_installed,
                "cuda_available": faster_whisper_cuda,
                "models": [
                    "small",
                    "medium",
                    "large-v3-turbo",
                    "large-v3",
                ],
                "providers": [
                    {
                        "id": "faster_whisper",
                        "label": "Faster-Whisper",
                        "installed": faster_whisper_installed,
                        "cuda_available": faster_whisper_cuda,
                        "models": ["small", "medium", "large-v3-turbo", "large-v3"],
                    },
                ],
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

    async def bulk_upload_handler(
        files: list[UploadFile] = UPLOAD_FILES,
    ) -> BulkUploadResponse:
        results: list[BulkUploadItemResult] = []
        for index, file in enumerate(files):
            name = file.filename or "video.mp4"
            try:
                upload = await upload_store.save(file)
                results.append(
                    BulkUploadItemResult(
                        index=index,
                        name=upload.name,
                        ok=True,
                        upload=upload,
                    )
                )
            except (OSError, ValueError) as exc:
                results.append(
                    BulkUploadItemResult(
                        index=index,
                        name=name,
                        ok=False,
                        error=str(exc),
                    )
                )
        succeeded = sum(result.ok for result in results)
        return BulkUploadResponse(
            results=results,
            succeeded=succeeded,
            failed=len(results) - succeeded,
        )

    async def create_job_handler(request: JobCreateRequest) -> JobView:
        try:
            return manager.create(request)
        except (ValueError, KeyError, OSError) as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    async def list_jobs_handler(
        cursor: Annotated[str | None, Query(max_length=2048)] = None,
        limit: Annotated[int | None, Query(ge=1, le=200)] = None,
        status_filter: Annotated[
            list[JobStatus] | None,
            Query(alias="status"),
        ] = None,
        batch_id: Annotated[str | None, Query(max_length=96)] = None,
        q: Annotated[str | None, Query(max_length=200)] = None,
        updated_after: datetime | None = None,
    ) -> list[JobView] | JobSummaryPage:
        summary_requested = any(
            value is not None
            for value in (
                cursor,
                limit,
                status_filter,
                batch_id,
                q,
                updated_after,
            )
        )
        if not summary_requested:
            return manager.list()
        try:
            return manager.list_summary_page(
                cursor=cursor,
                limit=limit or 50,
                statuses=set(status_filter) if status_filter is not None else None,
                batch_id=batch_id,
                query=q,
                updated_after=updated_after,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc

    async def get_job_handler(job_id: str) -> JobView:
        try:
            return manager.get(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    async def run_job_handler(job_id: str, request: JobRunRequest) -> JobView:
        try:
            return manager.run(job_id, request)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    async def cancel_job_handler(job_id: str) -> JobView:
        try:
            return manager.cancel(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    async def bulk_job_action_handler(
        request: JobBulkActionRequest,
    ) -> JobBulkActionResponse:
        return batch_manager.bulk_action(request)

    async def update_job_step_handler(
        job_id: str,
        step: JobStep,
        request: JobStepConfigUpdate,
    ) -> JobView:
        try:
            return manager.update_step_config(job_id, step, request.config)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    async def run_job_step_handler(
        job_id: str,
        step: JobStep,
        request: JobRunRequest,
    ) -> JobView:
        try:
            return manager.run_step(job_id, step, request)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    async def delete_job_handler(job_id: str) -> JobDeleteResult:
        try:
            manager.delete(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        batch_manager.remove_job(job_id)
        return JobDeleteResult(deleted=True, job_id=job_id)

    async def preflight_batch_handler(
        request: BatchPreflightRequest,
    ) -> BatchPreflightResult:
        return batch_manager.preflight(request)

    async def create_batch_handler(request: BatchCreateRequest) -> BatchCreateResult:
        return batch_manager.create(request)

    async def list_batches_handler() -> list[BatchRecord]:
        return batch_manager.list()

    async def get_batch_handler(batch_id: str) -> BatchRecord:
        try:
            return batch_manager.get(batch_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    async def run_batch_handler(
        batch_id: str,
        request: BatchRunRequest | None = None,
    ) -> JobBulkActionResponse:
        try:
            return batch_manager.batch_action(batch_id, JobBulkAction.RUN, request)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    async def retry_batch_handler(
        batch_id: str,
        request: BatchRunRequest | None = None,
    ) -> JobBulkActionResponse:
        try:
            return batch_manager.batch_action(
                batch_id,
                JobBulkAction.RETRY_FAILED,
                request,
            )
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    async def cancel_batch_handler(batch_id: str) -> JobBulkActionResponse:
        try:
            return batch_manager.batch_action(batch_id, JobBulkAction.CANCEL)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    async def delete_batch_handler(
        batch_id: str,
        delete_jobs: bool = False,
    ) -> BatchDeleteResult:
        try:
            return batch_manager.delete(batch_id, delete_jobs=delete_jobs)
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
        "/uploads/bulk",
        bulk_upload_handler,
        methods=["POST"],
        response_model=BulkUploadResponse,
        tags=["uploads"],
    )
    api.add_api_route(
        "/jobs", create_job_handler, methods=["POST"], response_model=JobView, tags=["jobs"]
    )
    api.add_api_route(
        "/jobs",
        list_jobs_handler,
        methods=["GET"],
        response_model=list[JobView] | JobSummaryPage,
        tags=["jobs"],
    )
    api.add_api_route(
        "/jobs/bulk-actions",
        bulk_job_action_handler,
        methods=["POST"],
        response_model=JobBulkActionResponse,
        tags=["jobs"],
    )
    api.add_api_route(
        "/jobs/{job_id}", get_job_handler, methods=["GET"], response_model=JobView, tags=["jobs"]
    )
    api.add_api_route(
        "/jobs/{job_id}/run",
        run_job_handler,
        methods=["POST"],
        response_model=JobView,
        tags=["jobs"],
    )
    api.add_api_route(
        "/jobs/{job_id}/cancel",
        cancel_job_handler,
        methods=["POST"],
        response_model=JobView,
        tags=["jobs"],
    )
    api.add_api_route(
        "/jobs/{job_id}/steps/{step}/config",
        update_job_step_handler,
        methods=["PATCH"],
        response_model=JobView,
        tags=["jobs"],
    )
    api.add_api_route(
        "/jobs/{job_id}/steps/{step}/run",
        run_job_step_handler,
        methods=["POST"],
        response_model=JobView,
        tags=["jobs"],
    )
    api.add_api_route(
        "/jobs/{job_id}",
        delete_job_handler,
        methods=["DELETE"],
        response_model=JobDeleteResult,
        tags=["jobs"],
    )
    api.add_api_route(
        "/batches/preflight",
        preflight_batch_handler,
        methods=["POST"],
        response_model=BatchPreflightResult,
        tags=["batches"],
    )
    api.add_api_route(
        "/batches",
        create_batch_handler,
        methods=["POST"],
        response_model=BatchCreateResult,
        tags=["batches"],
    )
    api.add_api_route(
        "/batches",
        list_batches_handler,
        methods=["GET"],
        response_model=list[BatchRecord],
        tags=["batches"],
    )
    api.add_api_route(
        "/batches/{batch_id}",
        get_batch_handler,
        methods=["GET"],
        response_model=BatchRecord,
        tags=["batches"],
    )
    api.add_api_route(
        "/batches/{batch_id}/run",
        run_batch_handler,
        methods=["POST"],
        response_model=JobBulkActionResponse,
        tags=["batches"],
    )
    api.add_api_route(
        "/batches/{batch_id}/retry-failed",
        retry_batch_handler,
        methods=["POST"],
        response_model=JobBulkActionResponse,
        tags=["batches"],
    )
    api.add_api_route(
        "/batches/{batch_id}/cancel",
        cancel_batch_handler,
        methods=["POST"],
        response_model=JobBulkActionResponse,
        tags=["batches"],
    )
    api.add_api_route(
        "/batches/{batch_id}",
        delete_batch_handler,
        methods=["DELETE"],
        response_model=BatchDeleteResult,
        tags=["batches"],
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
            "/jobs/{job_id}/run", run_job_handler, methods=["POST"], include_in_schema=False
        )
        app.add_api_route(
            "/jobs/{job_id}/steps/{step}/config",
            update_job_step_handler,
            methods=["PATCH"],
            include_in_schema=False,
        )
        app.add_api_route(
            "/jobs/{job_id}/steps/{step}/run",
            run_job_step_handler,
            methods=["POST"],
            include_in_schema=False,
        )
        app.add_api_route(
            "/jobs/{job_id}", delete_job_handler, methods=["DELETE"], include_in_schema=False
        )
        app.add_api_route(
            "/pick-video", pick_video_handler, methods=["POST"], include_in_schema=False
        )
        app.add_api_route(
            "/open-folder", open_folder_handler, methods=["POST"], include_in_schema=False
        )

    repository_root = Path(__file__).resolve().parents[4]
    dist = (static_dir or repository_root / "apps" / "web" / "dist").resolve()
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
