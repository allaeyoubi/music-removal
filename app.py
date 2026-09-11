"""Local web interface for the Music Removal processor.

This MVP is deliberately configured for one video at a time. It is suitable
for sharing with a friend to run on their own computer, not for public hosting
without authentication, cloud storage, and a real job queue.
"""

from __future__ import annotations

import os
import shutil
import threading
import time
import uuid
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal, TypedDict
from urllib.parse import urlparse

import yt_dlp
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from first import ProcessingCancelled, remux, remove_background_music

BASE_DIR = Path(__file__).parent
JOBS_DIR = Path(os.getenv("MUSIC_REMOVAL_DATA_DIR", BASE_DIR / "data" / "jobs"))
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "500"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
ALLOWED_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}


class Job(TypedDict):
    status: Literal["queued", "processing", "cancelling", "cancelled", "complete", "failed"]
    message: str
    output_name: str | None
    progress: int


app = FastAPI(title="Music Removal")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
jobs: dict[str, Job] = {}
jobs_lock = threading.Lock()
cancel_events: dict[str, threading.Event] = {}
worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="music-removal")
logger = logging.getLogger(__name__)


def update_job(
    job_id: str,
    status: Job["status"],
    message: str,
    progress: int,
    output_name: str | None = None,
) -> None:
    with jobs_lock:
        jobs[job_id] = {
            "status": status,
            "message": message,
            "output_name": output_name,
            "progress": progress,
        }


def process_job(
    job_id: str,
    source_path: Path,
    output_path: Path,
    workdir: Path,
    cancel_event: threading.Event,
) -> None:
    separation_started = threading.Event()
    separation_finished = threading.Event()

    def report_separation_status() -> None:
        started_at = time.monotonic()
        while not separation_finished.wait(5):
            if separation_started.is_set() and not cancel_event.is_set():
                elapsed = int(time.monotonic() - started_at)
                minutes, seconds = divmod(elapsed, 60)
                with jobs_lock:
                    current_job = jobs.get(job_id)
                if current_job is not None and current_job["progress"] == 22:
                    update_job(
                        job_id,
                        "processing",
                        f"Separating voice from background music… ({minutes}m {seconds:02d}s)",
                        22,
                    )

    heartbeat = threading.Thread(target=report_separation_status, daemon=True)
    heartbeat.start()

    def report(message: str, progress: int) -> None:
        if cancel_event.is_set():
            raise ProcessingCancelled("Processing was cancelled.")
        if "Separating voice" in message:
            separation_started.set()
        update_job(job_id, "processing", message, progress)

    try:
        report("Preparing your video…", 3)
        remove_background_music(source_path, output_path, workdir, progress=report, cancel_event=cancel_event)
        update_job(job_id, "complete", "Your music-free video is ready.", 100, output_path.name)
    except ProcessingCancelled:
        update_job(job_id, "cancelled", "Processing was cancelled. You can upload another video whenever you are ready.", 0)
    except RuntimeError as error:
        logger.exception("Processing failed for job %s", job_id)
        update_job(job_id, "failed", str(error), 0)
    except Exception:
        # Server logs retain the technical error while the browser receives a safe message.
        logger.exception("Processing failed for job %s", job_id)
        update_job(job_id, "failed", "We could not process this video. Please try another file.", 0)
    finally:
        separation_finished.set()


def download_and_process_job(
    job_id: str,
    youtube_url: str,
    job_dir: Path,
    output_path: Path,
    cancel_event: threading.Event,
) -> None:
    source_stem = job_dir / "youtube-source"

    def report_download(download_status: dict) -> None:
        if cancel_event.is_set():
            raise ProcessingCancelled("Processing was cancelled.")
        if download_status.get("status") == "downloading":
            downloaded = download_status.get("downloaded_bytes", 0)
            total = download_status.get("total_bytes") or download_status.get("total_bytes_estimate")
            progress = 2 if not total else min(20, max(2, int(downloaded / total * 20)))
            update_job(job_id, "processing", "Downloading your YouTube video…", progress)

    try:
        update_job(job_id, "processing", "Connecting to YouTube…", 1)
        download_options = {
            "format": "bv*+ba/b",
            "merge_output_format": "mp4",
            "outtmpl": str(source_stem) + ".%(ext)s",
            "noplaylist": True,
            "progress_hooks": [report_download],
            "quiet": True,
            "no_warnings": True,
        }
        download_error = None
        for client in (None, ["web_safari"]):
            if cancel_event.is_set():
                raise ProcessingCancelled("Processing was cancelled.")
            for partial_file in job_dir.glob("youtube-source.*"):
                partial_file.unlink(missing_ok=True)
            options = download_options.copy()
            if client is not None:
                options["extractor_args"] = {"youtube": {"player_client": client}}
            try:
                with yt_dlp.YoutubeDL(options) as downloader:
                    downloader.download([youtube_url])
                break
            except yt_dlp.utils.DownloadError as error:
                download_error = error
        else:
            raise download_error or RuntimeError("YouTube did not provide a downloadable video.")
        source_candidates = sorted(job_dir.glob("youtube-source.*"))
        if not source_candidates:
            raise RuntimeError("YouTube did not provide a downloadable video.")
        process_job(job_id, source_candidates[0], output_path, job_dir / "work", cancel_event)
    except ProcessingCancelled:
        update_job(job_id, "cancelled", "Processing was cancelled. You can upload another video whenever you are ready.", 0)
    except yt_dlp.utils.DownloadError as error:
        logger.exception("YouTube job failed for %s", job_id)
        message = str(error)
        if "not available" in message.lower() or "no formats" in message.lower():
            message = "YouTube does not provide a downloadable video for this link. It may be private, restricted, or unavailable."
        else:
            message = "YouTube could not download this link. Check that it opens normally and try again."
        update_job(job_id, "failed", message, 0)
    except Exception:
        logger.exception("YouTube job failed for %s", job_id)
        update_job(job_id, "failed", "We could not download or process this YouTube video.", 0)


def finish_recovered_job(
    job_id: str,
    source_path: Path,
    output_path: Path,
    vocals_path: Path,
    cancel_event: threading.Event,
) -> None:
    """Finish a Demucs task that survived a server restart."""
    deadline = time.monotonic() + (3 * 60 * 60)
    while not vocals_path.is_file() and time.monotonic() < deadline and not cancel_event.is_set():
        time.sleep(5)

    if cancel_event.is_set():
        update_job(job_id, "cancelled", "Processing was cancelled. You can upload another video whenever you are ready.", 0)
        return
    if not vocals_path.is_file():
        update_job(job_id, "failed", "Processing stopped before the audio separation finished.", 0)
        return

    try:
        update_job(job_id, "processing", "Creating your music-free video…", 92)
        remux(source_path, vocals_path, output_path, cancel_event)
        update_job(job_id, "complete", "Your music-free video is ready.", 100, output_path.name)
    except ProcessingCancelled:
        update_job(job_id, "cancelled", "Processing was cancelled. You can upload another video whenever you are ready.", 0)
    except Exception:
        logger.exception("Could not finish recovered job %s", job_id)
        update_job(job_id, "failed", "We could not finish this video. Please upload it again.", 0)


@app.on_event("startup")
def restore_completed_or_running_jobs() -> None:
    """Restore local job status after a development-server restart."""
    if not JOBS_DIR.exists():
        return

    for job_dir in JOBS_DIR.iterdir():
        if not job_dir.is_dir():
            continue
        cancel_event = cancel_events.setdefault(job_dir.name, threading.Event())
        sources = list(job_dir.glob("original.*"))
        if not sources:
            continue

        output_path = job_dir / "music-free-video.mp4"
        if output_path.is_file():
            update_job(job_dir.name, "complete", "Your music-free video is ready.", 100, output_path.name)
            continue

        workdir = job_dir / "work"
        vocals_path = workdir / "htdemucs" / "extracted_audio" / "vocals.wav"
        extracted_audio = workdir / "extracted_audio.wav"
        if extracted_audio.is_file():
            update_job(job_dir.name, "processing", "Separating voice from background music…", 22)
            worker.submit(finish_recovered_job, job_dir.name, sources[0], output_path, vocals_path, cancel_event)


@app.get("/", response_class=HTMLResponse)
def home() -> HTMLResponse:
    return HTMLResponse((BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8"))


@app.post("/api/jobs", status_code=202)
async def create_job(
    video: UploadFile | None = File(None),
    youtube_url: str = Form(""),
) -> dict[str, str]:
    youtube_url = youtube_url.strip()
    if youtube_url:
        parsed_url = urlparse(youtube_url)
        if parsed_url.scheme not in {"http", "https"} or parsed_url.hostname not in {
            "youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"
        }:
            raise HTTPException(status_code=422, detail="Paste a valid YouTube video link.")

        job_id = uuid.uuid4().hex
        job_dir = JOBS_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=False)
        output_path = job_dir / "music-free-video.mp4"
        update_job(job_id, "queued", "Your YouTube video is waiting to download.", 0)
        cancel_event = threading.Event()
        cancel_events[job_id] = cancel_event
        worker.submit(download_and_process_job, job_id, youtube_url, job_dir, output_path, cancel_event)
        return {"job_id": job_id}

    if video is None:
        raise HTTPException(status_code=422, detail="Choose a video or paste a YouTube link.")
    extension = Path(video.filename or "").suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=415, detail="Upload an MP4, MOV, MKV, AVI, WebM, or M4V video.")

    job_id = uuid.uuid4().hex
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=False)
    source_path = job_dir / f"original{extension}"
    total = 0

    try:
        with source_path.open("wb") as destination:
            while chunk := await video.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail=f"Videos must be {MAX_UPLOAD_MB} MB or smaller.")
                destination.write(chunk)
    except HTTPException:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise
    except Exception:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise
    finally:
        await video.close()

    if total == 0:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise HTTPException(status_code=422, detail="The uploaded file is empty.")

    output_path = job_dir / "music-free-video.mp4"
    update_job(job_id, "queued", "Your video is waiting to be processed.", 0)
    cancel_event = threading.Event()
    cancel_events[job_id] = cancel_event
    worker.submit(process_job, job_id, source_path, output_path, job_dir / "work", cancel_event)
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> Job:
    with jobs_lock:
        job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Processing job not found.")
    return job


@app.delete("/api/jobs/{job_id}", status_code=202)
def cancel_job(job_id: str) -> Job:
    with jobs_lock:
        job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Processing job not found.")
    if job["status"] in {"complete", "failed", "cancelled"}:
        return job

    cancel_events.setdefault(job_id, threading.Event()).set()
    update_job(job_id, "cancelling", "Cancelling your video processing…", job["progress"])
    with jobs_lock:
        return jobs[job_id]


@app.get("/api/jobs/{job_id}/download")
def download_result(job_id: str) -> FileResponse:
    with jobs_lock:
        job = jobs.get(job_id)
    output_path = JOBS_DIR / job_id / "music-free-video.mp4"
    if job is None or job["status"] != "complete" or not output_path.is_file():
        raise HTTPException(status_code=404, detail="The processed video is not ready.")
    return FileResponse(output_path, media_type="video/mp4", filename="music-free-video.mp4")
