from __future__ import annotations

import contextlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
SIDECAR_SOURCE = REPO_ROOT / "apps" / "sidecar" / "src"
PROCESS_HELPER = Path(__file__).with_name("issue22_sidecar_process.py")


@dataclass
class SidecarProcess:
    process: subprocess.Popen[bytes]
    log_handle: IO[str]
    log_path: Path
    base_url: str
    run_id: str
    server_pid: int
    stopped: bool = False

    def stop(self, *, force: bool) -> None:
        if self.stopped:
            return
        termination_signal = (
            signal.SIGKILL if force and hasattr(signal, "SIGKILL") else signal.SIGTERM
        )
        if self.process.poll() is None:
            with contextlib.suppress(OSError):
                os.kill(self.server_pid, termination_signal)
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)
        self.log_handle.close()
        self.stopped = True

    def log_text(self) -> str:
        if not self.stopped:
            self.log_handle.flush()
        return self.log_path.read_text(encoding="utf-8", errors="replace")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _assert_sidecar_stopped(instance: SidecarProcess) -> None:
    assert instance.process.poll() is not None
    port = int(instance.base_url.rsplit(":", maxsplit=1)[1])
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        assert probe.connect_ex(("127.0.0.1", port)) != 0


def _start_sidecar(
    tmp_path: Path,
    data_dir: Path,
    marker_dir: Path,
    *,
    block_translation: bool,
    name: str,
) -> SidecarProcess:
    port = _free_port()
    run_id = name
    pid_path = tmp_path / f"{name}.pid"
    log_path = tmp_path / f"{name}.log"
    log_handle = log_path.open("w", encoding="utf-8")
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(SIDECAR_SOURCE), environment.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    command = [
        sys.executable,
        str(PROCESS_HELPER),
        "--data-dir",
        str(data_dir),
        "--marker-dir",
        str(marker_dir),
        "--pid-file",
        str(pid_path),
        "--port",
        str(port),
        "--run-id",
        run_id,
    ]
    if block_translation:
        command.append("--block-translation")
    process = subprocess.Popen(
        command,
        cwd=REPO_ROOT,
        env=environment,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 15
    with httpx.Client(trust_env=False) as health_client:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            try:
                response = health_client.get(f"{base_url}/api/health", timeout=0.5)
                if response.status_code == 200 and pid_path.exists():
                    return SidecarProcess(
                        process=process,
                        log_handle=log_handle,
                        log_path=log_path,
                        base_url=base_url,
                        run_id=run_id,
                        server_pid=int(pid_path.read_text(encoding="ascii")),
                    )
            except httpx.HTTPError:
                pass
            time.sleep(0.05)
    if process.poll() is None and pid_path.exists():
        with contextlib.suppress(OSError, ValueError):
            server_pid = int(pid_path.read_text(encoding="ascii"))
            termination_signal = (
                signal.SIGKILL if hasattr(signal, "SIGKILL") else signal.SIGTERM
            )
            os.kill(server_pid, termination_signal)
    if process.poll() is None:
        process.kill()
        process.wait(timeout=10)
    log_handle.close()
    raise AssertionError(
        f"Sidecar did not start:\n{log_path.read_text(encoding='utf-8', errors='replace')}"
    )


def _assert_response(
    response: httpx.Response, status_code: int = 200
) -> dict[str, Any]:
    assert response.status_code == status_code, response.text
    return response.json()


def _wait_job(
    client: httpx.Client,
    job_id: str,
    predicate: Callable[[dict[str, Any]], bool],
    *,
    description: str,
    timeout: float = 10,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    latest: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        latest = _assert_response(client.get(f"/api/jobs/{job_id}"))
        if predicate(latest):
            return latest
        time.sleep(0.03)
    raise AssertionError(f"Timed out waiting for {description}: {latest}")


def _step(job: dict[str, Any], step_id: str) -> dict[str, Any]:
    return next(item for item in job["steps"] if item["id"] == step_id)


def _run_step(
    client: httpx.Client,
    job_id: str,
    step: str,
    *,
    api_key: str | None = None,
) -> httpx.Response:
    payload: dict[str, Any] = {"continue_pipeline": False}
    if api_key is not None:
        payload["api_key"] = api_key
    return client.post(f"/api/jobs/{job_id}/steps/{step}/run", json=payload)


def _persisted_text(data_dir: Path) -> str:
    return "\n".join(
        path.read_text(encoding="utf-8") for path in data_dir.rglob("*.json")
    )


def _marker_rows(marker_dir: Path) -> list[dict[str, Any]]:
    marker = marker_dir / "starts.ndjson"
    if not marker.exists():
        return []
    return [
        json.loads(line) for line in marker.read_text(encoding="utf-8").splitlines()
    ]


def test_issue22_real_process_recovery_secret_boundary_and_100_job_baseline(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    marker_dir = tmp_path / "markers"
    media_dir = tmp_path / "media"
    output_root = tmp_path / "outputs"
    media_dir.mkdir()
    output_root.mkdir()
    videos = {
        name: media_dir / name
        for name in ("running.mp4", "deepseek.mp4", "queued-a.mp4", "queued-b.mp4")
    }
    for path in videos.values():
        path.write_bytes(b"process-test-video")

    first_secret = "issue22-first-runtime-secret-must-never-persist"
    replacement_secret = "issue22-replacement-secret-must-never-persist"
    first: SidecarProcess | None = None
    second: SidecarProcess | None = None
    try:
        first = _start_sidecar(
            tmp_path,
            data_dir,
            marker_dir,
            block_translation=True,
            name="sidecar-before-crash",
        )
        with httpx.Client(
            base_url=first.base_url,
            timeout=15,
            trust_env=False,
        ) as client:

            def create_job(name: str, *, deepseek: bool = False) -> dict[str, Any]:
                payload: dict[str, Any] = {
                    "video_path": str(videos[name]),
                    "export": {"output_directory": str(output_root / name)},
                }
                if deepseek:
                    payload["translation"] = {
                        "provider": "deepseek",
                        "model": "deepseek-v4-flash",
                    }
                return _assert_response(client.post("/api/jobs", json=payload))

            running = create_job("running.mp4")
            deepseek = create_job("deepseek.mp4", deepseek=True)
            queued_a = create_job("queued-a.mp4")
            queued_b = create_job("queued-b.mp4")

            for job in (running, deepseek):
                for step_id in ("media", "transcription"):
                    _assert_response(_run_step(client, job["id"], step_id))
                    _wait_job(
                        client,
                        job["id"],
                        lambda view, current=step_id: (
                            _step(view, current)["status"] == "succeeded"
                        ),
                        description=f"{job['id']} {step_id} success",
                    )

            _assert_response(_run_step(client, running["id"], "translation"))
            _wait_job(
                client,
                running["id"],
                lambda view: view["status"] == "running",
                description="translation to enter running state",
            )

            deepseek_response = _run_step(
                client,
                deepseek["id"],
                "translation",
                api_key=first_secret,
            )
            _assert_response(deepseek_response)
            assert first_secret not in deepseek_response.text
            _assert_response(_run_step(client, queued_a["id"], "media"))
            _assert_response(_run_step(client, queued_b["id"], "media"))

            positions = [
                _assert_response(client.get(f"/api/jobs/{job_id}"))["queue_position"]
                for job_id in (deepseek["id"], queued_a["id"], queued_b["id"])
            ]
            assert positions == [1, 2, 3]
            assert first_secret not in _persisted_text(data_dir)

        first.stop(force=True)
        _assert_sidecar_stopped(first)
        assert first_secret not in first.log_text()

        second = _start_sidecar(
            tmp_path,
            data_dir,
            marker_dir,
            block_translation=False,
            name="sidecar-after-crash",
        )
        with httpx.Client(
            base_url=second.base_url,
            timeout=30,
            trust_env=False,
        ) as client:
            interrupted = _wait_job(
                client,
                running["id"],
                lambda view: view["status"] == "interrupted",
                description="running job to become interrupted",
            )
            assert _step(interrupted, "media")["artifact"] is not None
            assert _step(interrupted, "transcription")["artifact"] is not None
            translation = _step(interrupted, "translation")
            assert translation["status"] == "interrupted"
            assert translation["attempts"][-1]["status"] == "interrupted"

            waiting = _wait_job(
                client,
                deepseek["id"],
                lambda view: view["status"] == "waiting_for_input",
                description="DeepSeek job to wait for a replacement key",
            )
            assert waiting["queue_status"] == "waiting_for_input"
            assert _step(waiting, "media")["artifact"] is not None
            assert _step(waiting, "transcription")["artifact"] is not None

            for job in (queued_a, queued_b):
                _wait_job(
                    client,
                    job["id"],
                    lambda view: _step(view, "media")["status"] == "succeeded",
                    description=f"{job['id']} restored media success",
                )
            recovered_media = [
                row["job_id"]
                for row in _marker_rows(marker_dir)
                if row["run_id"] == second.run_id and row["step"] == "media"
            ]
            assert recovered_media == [queued_a["id"], queued_b["id"]]

            missing_key = _run_step(client, deepseek["id"], "translation")
            assert missing_key.status_code == 400
            assert "API Key" in missing_key.text
            replacement = _run_step(
                client,
                deepseek["id"],
                "translation",
                api_key=replacement_secret,
            )
            _assert_response(replacement)
            assert replacement_secret not in replacement.text
            _wait_job(
                client,
                deepseek["id"],
                lambda view: _step(view, "translation")["status"] == "succeeded",
                description="DeepSeek translation with replacement key",
            )

            stress_dir = media_dir / "stress"
            stress_output = output_root / "stress"
            stress_dir.mkdir()
            stress_output.mkdir()
            stress_videos = [
                stress_dir / f"stress-{index:03d}.mp4" for index in range(100)
            ]
            for path in stress_videos:
                path.write_bytes(b"stress-video")
            batch_payload = {
                "name": "Issue 22 · 100 Job baseline",
                "sources": [{"video_path": str(path)} for path in stress_videos],
                "config": {
                    "target_language": "zh-CN",
                    "translation": {
                        "target_language": "zh-CN",
                        "provider": "codex_spark",
                    },
                    "export": {"output_directory": str(stress_output)},
                },
                "auto_start": False,
            }
            create_started = time.perf_counter()
            batch_response = client.post("/api/batches", json=batch_payload)
            create_elapsed = time.perf_counter() - create_started
            batch_result = _assert_response(batch_response)
            assert batch_result["created_count"] == 100
            assert batch_result["failed_count"] == 0
            assert len(batch_result["batch"]["job_ids"]) == 100
            assert create_elapsed < 30

            batch_id = batch_result["batch"]["id"]
            summaries: list[dict[str, Any]] = []
            cursor: str | None = None
            summary_started = time.perf_counter()
            while True:
                params: dict[str, Any] = {"limit": 37}
                if cursor:
                    params["cursor"] = cursor
                else:
                    params["batch_id"] = batch_id
                page_response = client.get("/api/jobs", params=params)
                page = _assert_response(page_response)
                assert len(page_response.content) < 250_000
                summaries.extend(page["items"])
                cursor = page["next_cursor"] if page["has_more"] else None
                if cursor is None:
                    break
            summary_elapsed = time.perf_counter() - summary_started
            assert len(summaries) == len({item["id"] for item in summaries}) == 100
            assert all(item["status"] == "draft" for item in summaries)
            assert all(
                {"logs", "steps", "attempts", "api_key"}.isdisjoint(item)
                for item in summaries
            )
            assert summary_elapsed < 5

            search = _assert_response(
                client.get(
                    "/api/jobs",
                    params={"limit": 200, "batch_id": batch_id, "q": "stress-099"},
                )
            )
            assert search["total"] == 1
            assert search["items"][0]["source_name"] == "stress-099.mp4"

            persisted = _persisted_text(data_dir)
            assert first_secret not in persisted
            assert replacement_secret not in persisted
            assert len(list((data_dir / "jobs").glob("*/job.json"))) == 104
    finally:
        if first is not None:
            first.stop(force=True)
        if second is not None:
            second.stop(force=False)

    assert second is not None
    _assert_sidecar_stopped(second)
    assert first_secret not in second.log_text()
    assert replacement_secret not in second.log_text()
