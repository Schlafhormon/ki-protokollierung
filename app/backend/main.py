"""
FastAPI Backend for Meeting Minutes Generator
"""

import os
import re
import json
import uuid
import time
import logging
import mimetypes
import asyncio
import threading
import unicodedata
from collections import OrderedDict
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import (
    FastAPI,
    UploadFile,
    File,
    Form,
    HTTPException,
    Header,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field

from transcribe import (
    transcribe_audio,
    load_models,
    TranscriptionModels,
    _cleanup_memory,
    WHISPER_MODEL,
    WHISPER_BATCH_SIZE,
)
from summarize import (
    LLMCallError,
    StructuredOutputError,
    build_summary_review,
    summarize_segment,
)
from extract_tops import extract_tops_from_pdf
from assignment_suggestions import TranscriptUtterance, suggest_assignments
from agenda_detection import detect_agenda_from_transcript, segment_known_agenda
from export_protocol import (
    ProtocolAppendix,
    ProtocolMetadata,
    TranscriptLine as ExportTranscriptLine,
    build_protocol_document,
    render_protocol,
)
from telemetry import TelemetryCollector
from persistence import (
    anonymize_speaker_observations_for_profile,
    archive_speaker_profile,
    confirm_speaker_observation,
    create_speaker_profile,
    delete_speaker_embeddings,
    init_db,
    load_job,
    load_job_speaker_embedding,
    load_jobs,
    load_session,
    load_speaker_embeddings,
    load_speaker_observation,
    load_speaker_observations,
    load_speaker_profile,
    load_speaker_profiles,
    mark_interrupted_jobs,
    mark_interrupted_pipeline_jobs,
    reject_speaker_observation,
    load_pipeline_job,
    save_pipeline_job,
    save_job,
    save_job_speaker_embedding,
    save_session,
    save_speaker_embedding,
    save_speaker_observation,
    update_speaker_profile,
)
from speaker_recognition import (
    LocalSpeakerEmbedding,
    build_profile_references,
    match_speaker_embeddings,
    speaker_embedding_config_from_env,
)

# Configure logging with timestamps
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


class CancellationRequested(Exception):
    """Raised inside a transcription worker when a job has been cancelled."""


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan event handler.
    Loads all ML models at startup and cleans up on shutdown.
    """
    logger.info("Server starting up - initializing persistence...")

    try:
        init_db()
        mark_interrupted_jobs()
        mark_interrupted_pipeline_jobs()
        jobs.clear()
        jobs.update(load_jobs())
        logger.info(f"Loaded {len(jobs)} persisted jobs")
    except Exception as e:
        logger.error(f"Failed to initialize persistence: {e}", exc_info=True)

    logger.info("Loading transcription models...")

    try:
        # Load models at startup (this takes several minutes)
        app.state.models = load_models()
        app.state.models_loaded = True
        logger.info("Models loaded successfully - server ready")
    except Exception as e:
        logger.error(f"Failed to load models: {e}", exc_info=True)
        app.state.models = None
        app.state.models_loaded = False

    app.state.job_manager = TranscriptionJobManager(
        models_provider=lambda: getattr(app.state, "models", None),
        concurrency_limit=TRANSCRIPTION_CONCURRENCY,
    )
    await app.state.job_manager.start()
    app.state.pipeline_manager = PipelineJobManager(
        models_provider=lambda: getattr(app.state, "models", None),
        concurrency_limit=PIPELINE_CONCURRENCY,
    )
    await app.state.pipeline_manager.start()

    yield  # Server is running

    # Cleanup on shutdown - properly release GPU resources
    logger.info("Server shutting down - cleaning up...")
    if hasattr(app.state, "pipeline_manager"):
        await app.state.pipeline_manager.stop()
    if hasattr(app.state, "job_manager"):
        await app.state.job_manager.stop()
    mark_active_jobs_failed("Transkription wurde durch Backend-Shutdown unterbrochen")
    if hasattr(app.state, "models") and app.state.models is not None:
        device = app.state.models.device
        del app.state.models
        _cleanup_memory(device)
    app.state.models = None
    app.state.models_loaded = False


app = FastAPI(
    title="Protokollierungsassistenz API",
    description="API für die automatische Erstellung von Sitzungsprotokollen",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS configuration - allow configurable origins via environment
CORS_ORIGINS = os.environ.get(
    "CORS_ORIGINS",
    "http://localhost:5173,http://localhost:5174,http://localhost:5175,http://localhost:3000",
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Job cleanup configuration
JOB_MAX_AGE_SECONDS = int(os.environ.get("JOB_MAX_AGE_SECONDS", "7200"))  # 2 hours
JOB_MAX_COUNT = int(os.environ.get("JOB_MAX_COUNT", "100"))
DELETE_UPLOADS_ON_JOB_CLEANUP = (
    os.environ.get("DELETE_UPLOADS_ON_JOB_CLEANUP", "false").lower() == "true"
)
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(500 * 1024 * 1024)))
UPLOAD_CHUNK_SIZE = int(os.environ.get("UPLOAD_CHUNK_SIZE", str(1024 * 1024)))
TRANSCRIPTION_CONCURRENCY = int(os.environ.get("TRANSCRIPTION_CONCURRENCY", "1"))
PIPELINE_CONCURRENCY = int(os.environ.get("PIPELINE_CONCURRENCY", "1"))
DELETE_UPLOADS_ON_CANCEL_OR_FAILURE = (
    os.environ.get("DELETE_UPLOADS_ON_CANCEL_OR_FAILURE", "true").lower() == "true"
)

JOB_STATUS_PENDING = "pending"
JOB_STATUS_PROCESSING = "processing"
JOB_STATUS_COMPLETED = "completed"
JOB_STATUS_FAILED = "failed"
JOB_STATUS_CANCELLED = "cancelled"
TERMINAL_JOB_STATUSES = {
    JOB_STATUS_COMPLETED,
    JOB_STATUS_FAILED,
    JOB_STATUS_CANCELLED,
}
PIPELINE_STATUS_PENDING = "pending"
PIPELINE_STATUS_PROCESSING = "processing"
PIPELINE_STATUS_COMPLETED = "completed"
PIPELINE_STATUS_FAILED = "failed"
PIPELINE_STATUS_CANCELLED = "cancelled"
TERMINAL_PIPELINE_STATUSES = {
    PIPELINE_STATUS_COMPLETED,
    PIPELINE_STATUS_FAILED,
    PIPELINE_STATUS_CANCELLED,
}
PIPELINE_STAGE_UPLOAD = "upload"
PIPELINE_STAGE_TRANSCRIBE = "transcribe"
PIPELINE_STAGE_SPEAKER_MATCH = "speaker_match"
PIPELINE_STAGE_AGENDA_DETECT = "agenda_detect"
PIPELINE_STAGE_SUMMARIZE = "summarize"
PIPELINE_STAGE_READY_FOR_REVIEW = "ready_for_review"

# In-memory cache for jobs. SQLite remains the durable source for polling after
# restarts; the lock keeps the cache coherent across API and worker threads.
jobs: OrderedDict = OrderedDict()
JOB_LOCK = threading.RLock()

# Temporary upload directory
UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

ALLOWED_AUDIO_CONTENT_TYPES = {
    "audio/mpeg",
    "audio/wav",
    "audio/mp4",
    "audio/x-m4a",
    "audio/mp3",
}
ALLOWED_AUDIO_EXTENSIONS = (".mp3", ".wav", ".m4a")

CONTENT_TYPE_EXTENSIONS = {
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/wav": ".wav",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "application/pdf": ".pdf",
}


def is_allowed_audio_file(filename: str | None, content_type: str | None) -> bool:
    """Return whether an uploaded file is an accepted audio format."""
    normalized_filename = (filename or "").lower()
    return (content_type in ALLOWED_AUDIO_CONTENT_TYPES) or normalized_filename.endswith(
        ALLOWED_AUDIO_EXTENSIONS
    )


def is_allowed_pdf_file(filename: str | None, content_type: str | None) -> bool:
    """Return whether an uploaded file is a PDF."""
    return content_type == "application/pdf" or (filename or "").lower().endswith(".pdf")


def normalize_upload_filename(
    filename: str | None,
    *,
    default_stem: str,
    allowed_extensions: tuple[str, ...],
    content_type: str | None = None,
) -> str:
    """Return a safe display filename derived from an uploaded filename."""
    raw_filename = (filename or "").replace("\\", "/").split("/")[-1]
    normalized = unicodedata.normalize("NFKD", raw_filename)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_name = re.sub(r"[^A-Za-z0-9._-]+", "_", ascii_name).strip("._-")

    extension = Path(ascii_name).suffix.lower()
    if extension not in allowed_extensions:
        extension = CONTENT_TYPE_EXTENSIONS.get(content_type or "", "")
        if extension not in allowed_extensions:
            extension = ""

    stem = Path(ascii_name).stem if ascii_name else ""
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_-") or default_stem
    stem = stem[:80]
    return f"{stem}{extension}"


def upload_path_for(job_id: str, safe_filename: str) -> Path:
    """Build an upload path from trusted components only."""
    suffix = Path(safe_filename).suffix.lower()
    return UPLOAD_DIR / f"{job_id}{suffix}"


async def save_upload_with_size_limit(
    upload: UploadFile,
    destination: Path,
    *,
    max_bytes: int | None = None,
) -> int:
    """Stream an upload to disk while enforcing the backend upload limit."""
    max_allowed = max_bytes if max_bytes is not None else MAX_UPLOAD_BYTES
    destination.parent.mkdir(parents=True, exist_ok=True)
    bytes_written = 0

    try:
        with open(destination, "wb") as output:
            while True:
                chunk = await upload.read(UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                bytes_written += len(chunk)
                if bytes_written > max_allowed:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            "Datei ist zu groß. Maximal erlaubt sind "
                            f"{max_allowed // 1024 // 1024} MB."
                        ),
                    )
                output.write(chunk)
    except Exception:
        remove_upload_file(str(destination))
        raise

    return bytes_written


def remove_upload_file(file_path: str | None) -> None:
    if not file_path:
        return
    try:
        path = Path(file_path)
        if path.exists() and path.is_file():
            path.unlink()
    except Exception as e:
        logger.warning(f"Failed to remove upload file {file_path}: {e}")


def persist_job_state(job_id: str) -> None:
    """Persist a cached job without interrupting the request flow on DB errors."""
    with JOB_LOCK:
        job = jobs.get(job_id)
        if not job:
            return
        job["updated_at"] = time.time()
        job_snapshot = dict(job)
    try:
        save_job(job_id, job_snapshot)
    except Exception as e:
        logger.warning(f"Failed to persist job {job_id}: {e}")


def update_job_state(job_id: str, **changes: Any) -> dict[str, Any] | None:
    with JOB_LOCK:
        job = jobs.get(job_id)
        if job is None:
            return None
        job.update(changes)
        job["updated_at"] = time.time()
    persist_job_state(job_id)
    return get_job_from_cache_or_db(job_id)


def get_job_from_cache_or_db(job_id: str) -> dict[str, Any] | None:
    with JOB_LOCK:
        job = jobs.get(job_id)
        if job is not None:
            return job

    try:
        job = load_job(job_id)
    except Exception as e:
        logger.warning(f"Failed to load job {job_id} from persistence: {e}")
        return None

    if job is not None:
        with JOB_LOCK:
            jobs[job_id] = job
    return job


def is_job_cancelled(job_id: str) -> bool:
    job = get_job_from_cache_or_db(job_id)
    if not job:
        return True
    return bool(job.get("cancellation_requested")) or job.get("status") == JOB_STATUS_CANCELLED


def cleanup_job_uploads(job_id: str, job_data: dict[str, Any]) -> None:
    file_paths = {
        path for path in (job_data.get("audio_path"), job_data.get("file_path")) if path
    }
    for file_path in file_paths:
        remove_upload_file(file_path)
    if file_paths:
        with JOB_LOCK:
            job = jobs.get(job_id)
            if job:
                job["file_path"] = None
                job["audio_path"] = None
        persist_job_state(job_id)


def mark_active_jobs_failed(message: str) -> None:
    with JOB_LOCK:
        active_job_ids = [
            job_id
            for job_id, job in jobs.items()
            if job.get("status") in {JOB_STATUS_PENDING, JOB_STATUS_PROCESSING}
        ]

    for job_id in active_job_ids:
        update_job_state(
            job_id,
            status=JOB_STATUS_FAILED,
            progress=0,
            message=message,
            error=message,
        )


class TranscriptionJobManager:
    """Owns transcription queueing and worker concurrency for this process."""

    def __init__(
        self,
        *,
        models_provider: Callable[[], TranscriptionModels | None],
        concurrency_limit: int,
    ) -> None:
        self.models_provider = models_provider
        self.concurrency_limit = max(1, concurrency_limit)
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.executor = ThreadPoolExecutor(
            max_workers=self.concurrency_limit,
            thread_name_prefix="transcription-worker",
        )
        self.workers: list[asyncio.Task] = []
        self.started = False

    async def start(self) -> None:
        if self.started:
            return
        self.started = True
        self.workers = [
            asyncio.create_task(self._worker(index))
            for index in range(self.concurrency_limit)
        ]
        logger.info(
            "Transcription job manager started with concurrency=%s",
            self.concurrency_limit,
        )

    async def stop(self) -> None:
        if not self.started:
            return
        self.started = False
        for worker in self.workers:
            worker.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)
        self.workers = []
        self.executor.shutdown(wait=False, cancel_futures=True)

    async def enqueue(self, job_id: str) -> None:
        if not self.started:
            await self.start()
        await self.queue.put(job_id)

    async def _worker(self, worker_index: int) -> None:
        while True:
            job_id = await self.queue.get()
            try:
                job = get_job_from_cache_or_db(job_id)
                if not job:
                    continue
                if job.get("status") in TERMINAL_JOB_STATUSES:
                    continue
                if is_job_cancelled(job_id):
                    update_job_state(
                        job_id,
                        status=JOB_STATUS_CANCELLED,
                        message="Transkription abgebrochen",
                        error=None,
                    )
                    cleanup_job_uploads(job_id, job)
                    continue

                models = self.models_provider()
                if models is None:
                    update_job_state(
                        job_id,
                        status=JOB_STATUS_FAILED,
                        progress=0,
                        message="Modelle sind nicht geladen",
                        error="Modelle sind nicht geladen",
                    )
                    continue

                loop = asyncio.get_running_loop()
                await loop.run_in_executor(
                    self.executor,
                    run_transcription,
                    job_id,
                    job.get("file_path"),
                    models,
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(
                    "[Worker %s] Unhandled transcription worker error for job %s: %s",
                    worker_index,
                    job_id,
                    e,
                    exc_info=True,
                )
                update_job_state(
                    job_id,
                    status=JOB_STATUS_FAILED,
                    message=f"Fehler: {str(e)}",
                    error=str(e),
                )
            finally:
                self.queue.task_done()


async def get_or_create_job_manager() -> TranscriptionJobManager:
    manager = getattr(app.state, "job_manager", None)
    if manager is None:
        manager = TranscriptionJobManager(
            models_provider=lambda: getattr(app.state, "models", None),
            concurrency_limit=TRANSCRIPTION_CONCURRENCY,
        )
        app.state.job_manager = manager
    if not manager.started:
        await manager.start()
    return manager


def _pipeline_refs(job: dict[str, Any] | None) -> dict[str, Any]:
    return dict((job or {}).get("result_refs") or {})


def save_pipeline_state(pipeline_id: str, **changes: Any) -> dict[str, Any] | None:
    job = load_pipeline_job(pipeline_id)
    if job is None:
        return None
    result_refs = _pipeline_refs(job)
    if "result_refs" in changes:
        result_refs.update(changes.pop("result_refs") or {})
    job.update(changes)
    job["result_refs"] = result_refs
    job["updated_at"] = time.time()
    return save_pipeline_job(pipeline_id, job)


def append_pipeline_warning(pipeline_id: str, message: str) -> None:
    job = load_pipeline_job(pipeline_id)
    if job is None:
        return
    refs = _pipeline_refs(job)
    warnings = list(refs.get("warnings") or [])
    warnings.append(message)
    save_pipeline_state(pipeline_id, result_refs={"warnings": warnings})


def is_pipeline_cancelled(pipeline_id: str) -> bool:
    job = load_pipeline_job(pipeline_id)
    if job is None:
        return True
    refs = _pipeline_refs(job)
    return bool(refs.get("cancel_requested")) or job.get("status") == PIPELINE_STATUS_CANCELLED


def ensure_pipeline_not_cancelled(pipeline_id: str) -> None:
    if is_pipeline_cancelled(pipeline_id):
        raise CancellationRequested()


class PipelineJobManager:
    """Runs backend-controlled end-to-end protocol generation pipelines."""

    def __init__(
        self,
        *,
        models_provider: Callable[[], TranscriptionModels | None],
        concurrency_limit: int,
    ) -> None:
        self.models_provider = models_provider
        self.concurrency_limit = max(1, concurrency_limit)
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.executor = ThreadPoolExecutor(
            max_workers=self.concurrency_limit,
            thread_name_prefix="pipeline-worker",
        )
        self.workers: list[asyncio.Task] = []
        self.started = False

    async def start(self) -> None:
        if self.started:
            return
        self.started = True
        self.workers = [
            asyncio.create_task(self._worker(index))
            for index in range(self.concurrency_limit)
        ]
        logger.info(
            "Pipeline job manager started with concurrency=%s",
            self.concurrency_limit,
        )

    async def stop(self) -> None:
        if not self.started:
            return
        self.started = False
        for worker in self.workers:
            worker.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)
        self.workers = []
        self.executor.shutdown(wait=False, cancel_futures=True)

    async def enqueue(self, pipeline_id: str) -> None:
        if not self.started:
            await self.start()
        await self.queue.put(pipeline_id)

    async def _worker(self, worker_index: int) -> None:
        while True:
            pipeline_id = await self.queue.get()
            try:
                job = load_pipeline_job(pipeline_id)
                if not job or job.get("status") in TERMINAL_PIPELINE_STATUSES:
                    continue
                models = self.models_provider()
                if models is None:
                    save_pipeline_state(
                        pipeline_id,
                        status=PIPELINE_STATUS_FAILED,
                        error="Modelle sind nicht geladen",
                        progress=0,
                    )
                    continue
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(
                    self.executor,
                    run_pipeline_job,
                    pipeline_id,
                    models,
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(
                    "[Pipeline worker %s] Unhandled error for pipeline %s: %s",
                    worker_index,
                    pipeline_id,
                    e,
                    exc_info=True,
                )
                save_pipeline_state(
                    pipeline_id,
                    status=PIPELINE_STATUS_FAILED,
                    error=str(e),
                )
            finally:
                self.queue.task_done()


async def get_or_create_pipeline_manager() -> PipelineJobManager:
    manager = getattr(app.state, "pipeline_manager", None)
    if manager is None:
        manager = PipelineJobManager(
            models_provider=lambda: getattr(app.state, "models", None),
            concurrency_limit=PIPELINE_CONCURRENCY,
        )
        app.state.pipeline_manager = manager
    if not manager.started:
        await manager.start()
    return manager


def request_job_cancellation(job_id: str) -> dict[str, Any] | None:
    job = get_job_from_cache_or_db(job_id)
    if job is None:
        return None

    status = job.get("status")
    if status == JOB_STATUS_CANCELLED:
        return job
    if status in {JOB_STATUS_COMPLETED, JOB_STATUS_FAILED}:
        raise HTTPException(
            status_code=409,
            detail="Job kann in diesem Status nicht abgebrochen werden",
        )

    message = (
        "Transkription wird abgebrochen..."
        if status == JOB_STATUS_PROCESSING
        else "Transkription abgebrochen"
    )
    updated = update_job_state(
        job_id,
        status=JOB_STATUS_CANCELLED,
        cancellation_requested=True,
        message=message,
        error=None,
    )

    if status == JOB_STATUS_PENDING and updated:
        cleanup_job_uploads(job_id, updated)
        updated = get_job_from_cache_or_db(job_id)

    return updated


def get_audio_path_for_job(job: dict[str, Any]) -> str | None:
    """Return the persisted upload path used for playback, if available."""
    return job.get("audio_path") or job.get("file_path")


def audio_url_for_job(job_id: str, job: dict[str, Any]) -> str | None:
    audio_path = get_audio_path_for_job(job)
    if audio_path and os.path.exists(audio_path):
        return f"/api/audio/{job_id}"
    return None


def cleanup_old_jobs() -> int:
    """
    Remove old or excess jobs from memory.
    Upload files are retained by default so persisted sessions can be restored.
    Returns number of jobs removed.
    """
    now = time.time()
    removed = 0

    def cleanup_job_audio(job_id: str, job_data: dict) -> None:
        """Clean up audio file associated with a job."""
        if not DELETE_UPLOADS_ON_JOB_CLEANUP:
            return
        file_paths = {
            path
            for path in (job_data.get("audio_path"), job_data.get("file_path"))
            if path
        }
        for audio_path in file_paths:
            if not os.path.exists(audio_path):
                continue
            try:
                os.remove(audio_path)
                logger.info(f"Cleaned up audio file for job {job_id}")
            except Exception as e:
                logger.warning(f"Failed to clean up audio file for job {job_id}: {e}")

    with JOB_LOCK:
        # Remove jobs older than MAX_AGE
        jobs_to_remove = []
        for job_id, job_data in jobs.items():
            if now - job_data.get("created_at", now) > JOB_MAX_AGE_SECONDS:
                jobs_to_remove.append(job_id)

        for job_id in jobs_to_remove:
            cleanup_job_audio(job_id, jobs[job_id])
            del jobs[job_id]
            removed += 1

        # Remove oldest jobs if count exceeds MAX_COUNT
        while len(jobs) > JOB_MAX_COUNT:
            oldest_job_id = next(iter(jobs))
            cleanup_job_audio(oldest_job_id, jobs[oldest_job_id])
            del jobs[oldest_job_id]
            removed += 1

    if removed > 0:
        logger.info(f"Cleaned up {removed} old jobs, {len(jobs)} remaining")

    return removed


# ----- Pydantic Models -----


class TranscriptLine(BaseModel):
    speaker: str
    text: str
    start: float  # Start time in seconds
    end: float  # End time in seconds


class AudioMetadata(BaseModel):
    filename: Optional[str] = None
    content_type: Optional[str] = None
    size_bytes: Optional[int] = None


class SpeakerSuggestionResponse(BaseModel):
    observation_id: int
    local_speaker_id: str
    profile_id: str
    profile_display_name: str
    confidence: float
    status: str


class TranscriptionJob(BaseModel):
    job_id: str
    status: str  # "pending", "processing", "completed", "failed"
    progress: int
    message: str
    transcript: Optional[List[TranscriptLine]] = None
    speaker_suggestions: Optional[List[SpeakerSuggestionResponse]] = None
    audio_url: Optional[str] = None  # URL to stream audio for playback
    audio_metadata: Optional[AudioMetadata] = None
    error: Optional[str] = None


class PipelineStartResponse(BaseModel):
    pipeline_id: str
    session_id: str
    transcription_job_id: str
    status: str
    stage: str
    progress: int
    warnings: List[str] = Field(default_factory=list)


class PipelineStatusResponse(BaseModel):
    pipeline_id: str
    session_id: Optional[str] = None
    transcription_job_id: Optional[str] = None
    status: str
    stage: str
    progress: int
    warnings: List[str] = Field(default_factory=list)
    error: Optional[str] = None
    created_at: Optional[float] = None
    updated_at: Optional[float] = None


class SessionSaveRequest(BaseModel):
    session_id: Optional[str] = None
    job_id: Optional[str] = None
    current_step: Optional[int] = None
    tops: List[str] = Field(default_factory=list)
    transcript: Optional[List[TranscriptLine]] = None
    assignments: List[Optional[int]] = Field(default_factory=list)
    speaker_names: Dict[str, str] = Field(default_factory=dict)
    summaries: Dict[int, str] = Field(default_factory=dict)
    summary_reviews: Dict[int, Any] = Field(default_factory=dict)
    export_metadata: Dict[str, Any] = Field(default_factory=dict)
    skipped_assignment: bool = False


class SessionResponse(BaseModel):
    session_id: str
    job_id: Optional[str] = None
    current_step: Optional[int] = None
    tops: List[str] = Field(default_factory=list)
    assignments: List[Optional[int]] = Field(default_factory=list)
    speaker_names: Dict[str, str] = Field(default_factory=dict)
    summaries: Dict[int, str] = Field(default_factory=dict)
    summary_reviews: Dict[int, Any] = Field(default_factory=dict)
    export_metadata: Dict[str, Any] = Field(default_factory=dict)
    skipped_assignment: bool = False
    transcript: Optional[List[TranscriptLine]] = None
    audio_url: Optional[str] = None
    audio_metadata: Optional[AudioMetadata] = None
    job: Optional[TranscriptionJob] = None


class SpeakerProfileCreateRequest(BaseModel):
    display_name: str = Field(..., min_length=1)
    scope: Optional[str] = None


class SpeakerProfileUpdateRequest(BaseModel):
    display_name: Optional[str] = Field(None, min_length=1)
    scope: Optional[str] = None


class SpeakerProfileResponse(BaseModel):
    profile_id: str
    display_name: str
    scope: Optional[str] = None
    created_at: float
    updated_at: float
    archived_at: Optional[float] = None
    archived: bool = False


class SpeakerObservationResponse(BaseModel):
    observation_id: int
    job_id: str
    session_id: str
    local_speaker_id: str
    local_display_name: str
    profile_id: Optional[str] = None
    profile_display_name: Optional[str] = None
    profile: Optional[SpeakerProfileResponse] = None
    confidence: Optional[float] = None
    status: str
    display_name: str
    created_at: float
    updated_at: float


class PipelineResultResponse(BaseModel):
    pipeline: PipelineStatusResponse
    session: SessionResponse
    job: Optional[TranscriptionJob] = None
    speaker_observations: List[SpeakerObservationResponse] = Field(default_factory=list)
    summary_reviews: Dict[int, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)


class SpeakerObservationConfirmRequest(BaseModel):
    profile_id: Optional[str] = None
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)


class SpeakerObservationManualRequest(BaseModel):
    local_speaker_id: str = Field(..., min_length=1)
    profile_id: Optional[str] = None
    display_name: Optional[str] = Field(None, min_length=1)
    scope: Optional[str] = None
    confidence: Optional[float] = Field(default=1.0, ge=0.0, le=1.0)
    observation_id: Optional[int] = None


class SummarizeRequest(BaseModel):
    top_title: str
    lines: List[TranscriptLine]
    model: Optional[str] = None  # LLM model to use (e.g., "qwen3:8b")
    system_prompt: Optional[str] = None  # Custom system prompt


class StructuredSummaryResponse(BaseModel):
    discussion: List[str] = Field(default_factory=list)
    decisions: List[str] = Field(default_factory=list)
    votes: List[str] = Field(default_factory=list)
    action_items: List[str] = Field(default_factory=list)
    open_points: List[str] = Field(default_factory=list)
    uncertainties: List[str] = Field(default_factory=list)


class SummarySourceLinkResponse(BaseModel):
    section: str
    item_index: int
    item_text: str
    line_indices: List[int] = Field(default_factory=list)
    start: Optional[float] = None
    end: Optional[float] = None
    excerpt: str = ""
    confidence: float = 0.0
    missing_source: bool = False


class SummaryReviewWarningResponse(BaseModel):
    kind: str
    message: str
    severity: str = "warning"
    keyword: Optional[str] = None
    section: Optional[str] = None
    item_index: Optional[int] = None
    line_indices: List[int] = Field(default_factory=list)
    start: Optional[float] = None
    end: Optional[float] = None
    excerpt: str = ""


class SummarizeResponse(BaseModel):
    summary: str
    duration_seconds: float
    structured: Optional[StructuredSummaryResponse] = None
    source_links: List[SummarySourceLinkResponse] = Field(default_factory=list)
    review_warnings: List[SummaryReviewWarningResponse] = Field(default_factory=list)
    fallback_used: bool = False
    chunks_processed: int = 1


class ExtractTOPsResponse(BaseModel):
    tops: List[str]


class AssignmentSuggestionsRequest(BaseModel):
    transcript: List[TranscriptLine]
    tops: List[str]


class AgendaDetectionRequest(BaseModel):
    transcript: List[TranscriptLine]
    tops: List[str] = Field(default_factory=list)
    model: Optional[str] = None
    system_prompt: Optional[str] = None


class AssignmentSuggestionSegmentResponse(BaseModel):
    top_index: int
    top_title: str
    start_index: int
    end_index: int
    confidence: float
    uncertain: bool
    transition_type: str
    reason: str
    evidence_index: Optional[int] = None
    evidence_text: Optional[str] = None


class AssignmentSuggestionsResponse(BaseModel):
    suggested_assignments: List[Optional[int]]
    segments: List[AssignmentSuggestionSegmentResponse]
    strategy: str
    uncertain_count: int


class AgendaDetectionResponse(BaseModel):
    tops: List[str]
    assignments: List[Optional[int]]
    segments: List[AssignmentSuggestionSegmentResponse]
    uncertain_count: int
    strategy: str


class ExportMetadataRequest(BaseModel):
    committee: str = ""
    date: str = ""
    location: str = ""
    title: str = ""
    participants: List[str] = Field(default_factory=list)


class ExportAppendixRequest(BaseModel):
    include_speaker_list: bool = True
    include_transcript_excerpt: bool = False
    include_generation_note: bool = True
    transcript_excerpt_limit: int = Field(default=20, ge=1, le=200)


class ProtocolExportRequest(BaseModel):
    format: str = "docx"
    metadata: ExportMetadataRequest = Field(default_factory=ExportMetadataRequest)
    appendix: ExportAppendixRequest = Field(default_factory=ExportAppendixRequest)
    tops: List[str] = Field(default_factory=list)
    transcript: List[TranscriptLine] = Field(default_factory=list)
    assignments: List[Optional[int]] = Field(default_factory=list)
    speaker_names: Dict[str, str] = Field(default_factory=dict)
    summaries: Dict[int, str] = Field(default_factory=dict)
    summary_reviews: Dict[int, Any] = Field(default_factory=dict)


class SessionCompleteRequest(BaseModel):
    """Request model for reporting session completion with telemetry."""

    telemetry_consent: bool = False
    job_id: str
    top_count: int
    protocol_char_count: int
    summarization_duration_seconds: float
    llm_model: str
    system_prompt_kind: str = "custom"


class SessionCompleteResponse(BaseModel):
    success: bool
    message: str


def build_speaker_suggestion_responses(
    job_id: str,
    session_id: str | None,
) -> list[SpeakerSuggestionResponse] | None:
    if not session_id:
        return None

    observations = load_speaker_observations(
        job_id=job_id,
        session_id=session_id,
        status="suggested",
    )
    suggestions: list[SpeakerSuggestionResponse] = []
    for observation in observations:
        profile_id = observation.get("profile_id")
        confidence = observation.get("confidence")
        if not profile_id or confidence is None:
            continue
        profile = load_speaker_profile(profile_id, include_archived=True)
        if profile is None:
            continue
        suggestions.append(
            SpeakerSuggestionResponse(
                observation_id=observation["observation_id"],
                local_speaker_id=observation["local_speaker_id"],
                profile_id=profile_id,
                profile_display_name=profile["display_name"],
                confidence=confidence,
                status=observation["status"],
            )
        )

    return suggestions or None


def build_transcription_job_response(
    job_id: str, job: dict[str, Any]
) -> TranscriptionJob:
    audio_metadata = None
    if any(
        job.get(key)
        for key in ("audio_filename", "audio_content_type", "audio_size_bytes")
    ):
        audio_metadata = AudioMetadata(
            filename=job.get("audio_filename"),
            content_type=job.get("audio_content_type"),
            size_bytes=job.get("audio_size_bytes"),
        )

    return TranscriptionJob(
        job_id=job_id,
        status=job["status"],
        progress=job["progress"],
        message=job["message"],
        transcript=(
            [TranscriptLine(**line) for line in job["transcript"]]
            if job.get("transcript")
            else None
        ),
        speaker_suggestions=build_speaker_suggestion_responses(
            job_id,
            job.get("session_id"),
        ),
        audio_url=audio_url_for_job(job_id, job),
        audio_metadata=audio_metadata,
        error=job.get("error"),
    )


def build_session_response(session: dict[str, Any]) -> SessionResponse:
    job_id = session.get("job_id")
    job = get_job_from_cache_or_db(job_id) if job_id else None
    job_response = build_transcription_job_response(job_id, job) if job else None
    transcript = session.get("transcript")
    if transcript is None and job_response:
        transcript = job_response.transcript

    return SessionResponse(
        session_id=session["session_id"],
        job_id=job_id,
        current_step=session.get("current_step"),
        tops=session.get("tops") or [],
        assignments=session.get("assignments") or [],
        speaker_names=session.get("speaker_names") or {},
        summaries=session.get("summaries") or {},
        summary_reviews=session.get("summary_reviews") or {},
        export_metadata=session.get("export_metadata") or {},
        skipped_assignment=bool(session.get("skipped_assignment")),
        transcript=transcript,
        audio_url=job_response.audio_url if job_response else None,
        audio_metadata=job_response.audio_metadata if job_response else None,
        job=job_response,
    )


def build_speaker_profile_response(profile: dict[str, Any]) -> SpeakerProfileResponse:
    return SpeakerProfileResponse(
        profile_id=profile["profile_id"],
        display_name=profile["display_name"],
        scope=profile.get("scope"),
        created_at=profile["created_at"],
        updated_at=profile["updated_at"],
        archived_at=profile.get("archived_at"),
        archived=profile.get("archived_at") is not None,
    )


def build_speaker_observation_response(
    observation: dict[str, Any],
    session: dict[str, Any],
) -> SpeakerObservationResponse:
    profile = None
    if observation.get("profile_id"):
        profile = load_speaker_profile(
            observation["profile_id"],
            include_archived=True,
        )
    profile_response = build_speaker_profile_response(profile) if profile else None
    profile_display_name = profile.get("display_name") if profile else None
    local_display_name = (session.get("speaker_names") or {}).get(
        observation["local_speaker_id"],
        observation["local_speaker_id"],
    )
    display_name = profile_display_name or local_display_name

    return SpeakerObservationResponse(
        observation_id=observation["observation_id"],
        job_id=observation["job_id"],
        session_id=observation["session_id"],
        local_speaker_id=observation["local_speaker_id"],
        local_display_name=local_display_name,
        profile_id=observation.get("profile_id"),
        profile_display_name=profile_display_name,
        profile=profile_response,
        confidence=observation.get("confidence"),
        status=observation["status"],
        display_name=display_name,
        created_at=observation["created_at"],
        updated_at=observation["updated_at"],
    )


def get_required_session(session_id: str) -> dict[str, Any]:
    session = load_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session nicht gefunden")
    return session


def get_required_active_profile(profile_id: str) -> dict[str, Any]:
    profile = load_speaker_profile(profile_id, include_archived=True)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profil nicht gefunden")
    if profile.get("archived_at") is not None:
        raise HTTPException(status_code=409, detail="Profil ist archiviert")
    return profile


def validate_display_name(display_name: str) -> str:
    cleaned = display_name.strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="Anzeigename darf nicht leer sein")
    return cleaned


def known_local_speaker_ids(session: dict[str, Any]) -> set[str]:
    speaker_ids = set((session.get("speaker_names") or {}).keys())
    for line in session.get("transcript") or []:
        if line.get("speaker"):
            speaker_ids.add(str(line["speaker"]))
    return speaker_ids


def ensure_local_speaker_exists(session: dict[str, Any], local_speaker_id: str) -> None:
    if local_speaker_id not in known_local_speaker_ids(session):
        raise HTTPException(status_code=404, detail="Lokaler Sprecher nicht gefunden")


def get_observation_for_session(
    session_id: str,
    observation_id: int,
) -> dict[str, Any]:
    observation = load_speaker_observation(observation_id)
    if observation is None or observation.get("session_id") != session_id:
        raise HTTPException(status_code=404, detail="Observation nicht gefunden")
    return observation


def ensure_no_accepted_local_mapping(
    session_id: str,
    local_speaker_id: str,
    *,
    exclude_observation_id: int | None = None,
) -> None:
    observations = load_speaker_observations(session_id=session_id)
    for observation in observations:
        if observation["observation_id"] == exclude_observation_id:
            continue
        if observation.get("local_speaker_id") != local_speaker_id:
            continue
        if observation.get("status") in {"confirmed", "manual"}:
            raise HTTPException(
                status_code=409,
                detail="Lokaler Sprecher ist bereits einem Profil zugeordnet",
            )


def apply_profile_display_name_to_session(
    session: dict[str, Any],
    local_speaker_id: str,
    profile: dict[str, Any],
) -> dict[str, Any]:
    updated_session = dict(session)
    speaker_names = dict(updated_session.get("speaker_names") or {})
    speaker_names[local_speaker_id] = profile["display_name"]
    updated_session["speaker_names"] = speaker_names
    return save_session(session["session_id"], updated_session)


def persist_job_speaker_embeddings(
    job_id: str,
    embeddings: list[LocalSpeakerEmbedding] | None,
) -> None:
    for embedding in embeddings or []:
        save_job_speaker_embedding(
            job_id=job_id,
            local_speaker_id=embedding.local_speaker_id,
            embedding=embedding.embedding,
            model_name=embedding.model_name,
            quality=embedding.quality,
            quality_metadata=embedding.quality_metadata,
        )


def create_speaker_suggestion_observations(
    *,
    job_id: str,
    session_id: str | None,
    local_embeddings: list[LocalSpeakerEmbedding] | None,
    speaker_memory_opt_in: bool = False,
) -> None:
    if not speaker_memory_opt_in or not session_id or not local_embeddings:
        return

    config = speaker_embedding_config_from_env()
    profiles = load_speaker_profiles()
    matches = []
    model_names = sorted({embedding.model_name for embedding in local_embeddings})
    for model_name in model_names:
        model_local_embeddings = [
            embedding
            for embedding in local_embeddings
            if embedding.model_name == model_name
        ]
        embeddings_by_profile = {
            profile["profile_id"]: load_speaker_embeddings(
                profile["profile_id"],
                model_name=model_name,
            )
            for profile in profiles
        }
        references = build_profile_references(profiles, embeddings_by_profile)
        matches.extend(
            match_speaker_embeddings(
                model_local_embeddings,
                references,
                auto_threshold=config.auto_threshold,
                suggest_threshold=config.suggest_threshold,
            )
        )
    if not matches:
        return

    existing_observations = load_speaker_observations(
        job_id=job_id,
        session_id=session_id,
    )
    existing_keys = {
        (
            observation.get("local_speaker_id"),
            observation.get("profile_id"),
            observation.get("status"),
        )
        for observation in existing_observations
    }
    accepted_local_speakers = {
        observation.get("local_speaker_id")
        for observation in load_speaker_observations(session_id=session_id)
        if observation.get("status") in {"confirmed", "manual"}
    }

    for match in matches:
        if match.local_speaker_id in accepted_local_speakers:
            continue
        key = (match.local_speaker_id, match.profile_id, "suggested")
        if key in existing_keys:
            continue
        save_speaker_observation(
            job_id=job_id,
            session_id=session_id,
            local_speaker_id=match.local_speaker_id,
            profile_id=match.profile_id,
            confidence=match.confidence,
            status="suggested",
        )


def add_job_embedding_to_profile(
    *,
    job_id: str,
    local_speaker_id: str,
    profile_id: str,
    observation_id: int | None = None,
    storage_reason: str | None = None,
) -> None:
    if storage_reason not in {"opt_in", "confirm", "manual"}:
        raise HTTPException(
            status_code=403,
            detail=(
                "Globale Sprecher-Embeddings werden nur nach Opt-in oder "
                "expliziter Sprecheraktion gespeichert"
            ),
        )
    local_embedding = load_job_speaker_embedding(job_id, local_speaker_id)
    if local_embedding is None:
        return

    metadata = dict(local_embedding.get("quality_metadata") or {})
    metadata.update(
        {
            "source_job_id": job_id,
            "source_local_speaker_id": local_speaker_id,
            "source_observation_id": observation_id,
            "storage_reason": storage_reason,
        }
    )

    for existing in load_speaker_embeddings(
        profile_id,
        model_name=local_embedding["model_name"],
    ):
        existing_metadata = existing.get("metadata") or {}
        if (
            existing_metadata.get("source_job_id") == job_id
            and existing_metadata.get("source_local_speaker_id") == local_speaker_id
        ):
            return

    save_speaker_embedding(
        profile_id,
        local_embedding["embedding"],
        model_name=local_embedding["model_name"],
        quality=local_embedding.get("quality"),
        metadata=metadata,
    )


def model_to_dict(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def parse_pipeline_tops(raw_tops: str | None) -> list[str]:
    if not raw_tops:
        return []
    text = raw_tops.strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    except json.JSONDecodeError:
        pass
    separators = "\n" if "\n" in text else ","
    return [item.strip() for item in text.split(separators) if item.strip()]


def parse_pipeline_options(raw_options: str | None) -> dict[str, Any]:
    if not raw_options or not raw_options.strip():
        return {}
    try:
        parsed = json.loads(raw_options)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Optionen müssen valides JSON sein: {exc}",
        ) from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="Optionen müssen ein JSON-Objekt sein")
    return parsed


def build_pipeline_status_response(job: dict[str, Any]) -> PipelineStatusResponse:
    refs = _pipeline_refs(job)
    return PipelineStatusResponse(
        pipeline_id=job["pipeline_job_id"],
        session_id=job.get("session_id"),
        transcription_job_id=job.get("transcription_job_id"),
        status=job["status"],
        stage=job["stage"],
        progress=job["progress"],
        warnings=list(refs.get("warnings") or []),
        error=job.get("error"),
        created_at=job.get("created_at"),
        updated_at=job.get("updated_at"),
    )


def line_to_dict(line: Any) -> dict[str, Any]:
    if isinstance(line, dict):
        return {
            "speaker": str(line.get("speaker", "")),
            "text": str(line.get("text", "")),
            "start": float(line.get("start", 0)),
            "end": float(line.get("end", 0)),
        }
    return {
        "speaker": str(getattr(line, "speaker", "")),
        "text": str(getattr(line, "text", "")),
        "start": float(getattr(line, "start", 0)),
        "end": float(getattr(line, "end", 0)),
    }


def transcript_utterances(transcript: list[dict[str, Any]]) -> list[TranscriptUtterance]:
    return [
        TranscriptUtterance(speaker=line.get("speaker", ""), text=line.get("text", ""))
        for line in transcript
    ]


def save_pipeline_session(
    session_id: str,
    *,
    job_id: str | None = None,
    transcript: list[dict[str, Any]] | None = None,
    tops: list[str] | None = None,
    assignments: list[int | None] | None = None,
    summaries: dict[int, str] | None = None,
    summary_reviews: dict[int, Any] | None = None,
    current_step: int | None = None,
) -> dict[str, Any]:
    session = load_session(session_id) or {"session_id": session_id}
    state = dict(session)
    if job_id is not None:
        state["job_id"] = job_id
    if transcript is not None:
        state["transcript"] = transcript
        speaker_names = dict(state.get("speaker_names") or {})
        for line in transcript:
            speaker = str(line.get("speaker", "")).strip()
            if speaker:
                speaker_names.setdefault(speaker, speaker)
        state["speaker_names"] = speaker_names
    if tops is not None:
        state["tops"] = tops
    if assignments is not None:
        state["assignments"] = assignments
    if summaries is not None:
        state["summaries"] = summaries
    if summary_reviews is not None:
        state["summary_reviews"] = summary_reviews
    if current_step is not None:
        state["current_step"] = current_step
    state.setdefault("speaker_names", {})
    state.setdefault("tops", [])
    state.setdefault("assignments", [])
    state.setdefault("summaries", {})
    state.setdefault("summary_reviews", {})
    state.setdefault("export_metadata", {})
    state.setdefault("skipped_assignment", False)
    return save_session(session_id, state)


def fallback_agenda(
    transcript: list[dict[str, Any]],
    known_tops: list[str] | None = None,
) -> tuple[list[str], list[int | None], dict[str, Any]]:
    tops = [top.strip() for top in known_tops or [] if top.strip()]
    if not tops:
        tops = ["Gesamtes Gespräch"]
        return tops, [0 for _ in transcript], {
            "strategy": "pipeline_fallback_full_conversation",
            "segments": [],
            "uncertain_count": 1 if transcript else 0,
        }

    try:
        result = suggest_assignments(transcript_utterances(transcript), tops)
        assignments = result.suggested_assignments
        if not assignments or all(assignment is None for assignment in assignments):
            assignments = [0 if tops else None for _ in transcript]
        return tops, assignments, {
            "strategy": f"{result.strategy}_pipeline_fallback",
            "segments": [segment.__dict__ for segment in result.segments],
            "uncertain_count": result.uncertain_count,
        }
    except Exception:
        return tops, [0 if tops else None for _ in transcript], {
            "strategy": "pipeline_fallback_known_tops",
            "segments": [],
            "uncertain_count": len(tops),
        }


def detect_pipeline_agenda(
    pipeline_id: str,
    transcript: list[dict[str, Any]],
    *,
    known_tops: list[str],
    pdf_path: str | None,
    options: dict[str, Any],
) -> tuple[list[str], list[int | None], dict[str, Any]]:
    agenda_tops = [top.strip() for top in known_tops if top.strip()]
    model = options.get("agenda_model") or options.get("model")
    system_prompt = options.get("agenda_system_prompt") or options.get("system_prompt")

    if pdf_path:
        try:
            extracted_tops = extract_tops_from_pdf(
                pdf_path,
                model=model,
                system_prompt=system_prompt,
            )
            if extracted_tops:
                agenda_tops = [top.strip() for top in extracted_tops if top.strip()]
        except Exception as exc:
            append_pipeline_warning(
                pipeline_id,
                f"TOP-Erkennung aus PDF fehlgeschlagen, nutze Fallback: {exc}",
            )

    try:
        utterances = transcript_utterances(transcript)
        if agenda_tops:
            result = segment_known_agenda(
                utterances,
                agenda_tops,
                model=model,
                system_prompt=system_prompt,
            )
        else:
            result = detect_agenda_from_transcript(
                utterances,
                model=model,
                system_prompt=system_prompt,
            )
        if result.tops and result.assignments:
            return result.tops, result.assignments, {
                "strategy": result.strategy,
                "segments": [segment.__dict__ for segment in result.segments],
                "uncertain_count": result.uncertain_count,
            }
        append_pipeline_warning(
            pipeline_id,
            "Agenda Detection ergab keine belastbaren TOPs, nutze Fallback.",
        )
    except Exception as exc:
        append_pipeline_warning(
            pipeline_id,
            f"Agenda Detection fehlgeschlagen, nutze Fallback: {exc}",
        )

    return fallback_agenda(transcript, agenda_tops)


def summarize_pipeline_segments(
    pipeline_id: str,
    *,
    transcript: list[dict[str, Any]],
    tops: list[str],
    assignments: list[int | None],
    options: dict[str, Any],
) -> tuple[dict[int, str], dict[int, Any]]:
    summaries: dict[int, str] = {}
    summary_reviews: dict[int, Any] = {}
    model = options.get("summary_model") or options.get("model")
    system_prompt = options.get("summary_system_prompt") or options.get("system_prompt")

    for top_index, top_title in enumerate(tops):
        lines = [
            line
            for line_index, line in enumerate(transcript)
            if line_index < len(assignments) and assignments[line_index] == top_index
        ]
        if not lines:
            summaries[top_index] = ""
            summary_reviews[top_index] = {
                "structured": None,
                "source_links": [],
                "review_warnings": [
                    {
                        "kind": "empty_top_segment",
                        "message": "Für diesen TOP wurden keine Transkriptzeilen zugeordnet.",
                        "severity": "warning",
                        "line_indices": [],
                        "excerpt": "",
                    }
                ],
            }
            continue

        transcript_text = "\n".join(
            f"{line.get('speaker', '')}: {line.get('text', '')}" for line in lines
        )
        try:
            result = summarize_segment(
                top_title,
                transcript_text,
                model=model,
                system_prompt=system_prompt,
            )
            review = build_summary_review(
                structured=result.structured,
                summary=result.summary,
                lines=lines,
            )
            summaries[top_index] = result.summary
            summary_reviews[top_index] = {
                "structured": (
                    result.structured.to_dict() if result.structured is not None else None
                ),
                "source_links": [link.to_dict() for link in review.source_links],
                "review_warnings": [warning.to_dict() for warning in review.warnings],
                "fallback_used": result.fallback_used,
                "chunks_processed": result.chunks_processed,
                "duration_seconds": result.duration_seconds,
            }
        except Exception as exc:
            message = f"Zusammenfassung für TOP {top_index + 1} fehlgeschlagen: {exc}"
            append_pipeline_warning(pipeline_id, message)
            summaries[top_index] = ""
            summary_reviews[top_index] = {
                "structured": None,
                "source_links": [],
                "review_warnings": [
                    {
                        "kind": "summary_failed",
                        "message": message,
                        "severity": "error",
                        "line_indices": [],
                        "excerpt": "",
                    }
                ],
                "error": str(exc),
            }

    return summaries, summary_reviews


def run_pipeline_job(
    pipeline_id: str,
    models: TranscriptionModels,
) -> None:
    job = load_pipeline_job(pipeline_id)
    if job is None:
        return
    refs = _pipeline_refs(job)
    session_id = job.get("session_id")
    transcription_job_id = job.get("transcription_job_id")
    audio_path = refs.get("audio_path")
    pdf_path = refs.get("pdf_path")
    options = dict(refs.get("options") or {})
    known_tops = list(refs.get("known_tops") or [])

    if not session_id or not transcription_job_id:
        save_pipeline_state(
            pipeline_id,
            status=PIPELINE_STATUS_FAILED,
            error="Pipeline ist unvollständig initialisiert",
            progress=0,
        )
        return

    try:
        ensure_pipeline_not_cancelled(pipeline_id)
        save_pipeline_state(
            pipeline_id,
            status=PIPELINE_STATUS_PROCESSING,
            stage=PIPELINE_STAGE_TRANSCRIBE,
            progress=15,
            error=None,
        )
        run_transcription(transcription_job_id, audio_path, models)
        ensure_pipeline_not_cancelled(pipeline_id)

        transcription_job = load_job(transcription_job_id)
        if transcription_job is None or transcription_job.get("status") != JOB_STATUS_COMPLETED:
            error = (
                transcription_job.get("error")
                if transcription_job
                else "Transkriptionsjob nicht gefunden"
            )
            raise RuntimeError(error or "Transkription fehlgeschlagen")

        transcript = [
            line_to_dict(line) for line in (transcription_job.get("transcript") or [])
        ]
        save_pipeline_session(
            session_id,
            job_id=transcription_job_id,
            transcript=transcript,
            current_step=1,
        )
        save_pipeline_state(
            pipeline_id,
            stage=PIPELINE_STAGE_SPEAKER_MATCH,
            progress=62,
            result_refs={"transcript_line_count": len(transcript)},
        )

        try:
            suggestions = build_speaker_suggestion_responses(
                transcription_job_id,
                session_id,
            )
            save_pipeline_state(
                pipeline_id,
                result_refs={"speaker_suggestion_count": len(suggestions or [])},
            )
        except Exception as exc:
            append_pipeline_warning(
                pipeline_id,
                f"Sprecher-Matching konnte nicht ausgewertet werden: {exc}",
            )

        ensure_pipeline_not_cancelled(pipeline_id)
        save_pipeline_state(
            pipeline_id,
            stage=PIPELINE_STAGE_AGENDA_DETECT,
            progress=72,
        )
        tops, assignments, agenda_info = detect_pipeline_agenda(
            pipeline_id,
            transcript,
            known_tops=known_tops,
            pdf_path=pdf_path,
            options=options,
        )
        save_pipeline_session(
            session_id,
            job_id=transcription_job_id,
            transcript=transcript,
            tops=tops,
            assignments=assignments,
            current_step=2,
        )
        save_pipeline_state(
            pipeline_id,
            result_refs={"agenda": agenda_info, "top_count": len(tops)},
        )

        ensure_pipeline_not_cancelled(pipeline_id)
        save_pipeline_state(
            pipeline_id,
            stage=PIPELINE_STAGE_SUMMARIZE,
            progress=82,
        )
        summaries, summary_reviews = summarize_pipeline_segments(
            pipeline_id,
            transcript=transcript,
            tops=tops,
            assignments=assignments,
            options=options,
        )
        save_pipeline_session(
            session_id,
            job_id=transcription_job_id,
            transcript=transcript,
            tops=tops,
            assignments=assignments,
            summaries=summaries,
            summary_reviews=summary_reviews,
            current_step=3,
        )

        ensure_pipeline_not_cancelled(pipeline_id)
        save_pipeline_state(
            pipeline_id,
            status=PIPELINE_STATUS_COMPLETED,
            stage=PIPELINE_STAGE_READY_FOR_REVIEW,
            progress=100,
            error=None,
            result_refs={"ready_for_review": True},
        )
    except CancellationRequested:
        save_pipeline_state(
            pipeline_id,
            status=PIPELINE_STATUS_CANCELLED,
            error=None,
            result_refs={"cancel_requested": True},
        )
    except Exception as exc:
        logger.error("[Pipeline %s] failed: %s", pipeline_id, exc, exc_info=True)
        save_pipeline_state(
            pipeline_id,
            status=PIPELINE_STATUS_FAILED,
            error=str(exc),
        )
    finally:
        if pdf_path:
            remove_upload_file(pdf_path)


def request_pipeline_cancellation(pipeline_id: str) -> dict[str, Any] | None:
    job = load_pipeline_job(pipeline_id)
    if job is None:
        return None
    if job.get("status") == PIPELINE_STATUS_CANCELLED:
        return job
    if job.get("status") in {PIPELINE_STATUS_COMPLETED, PIPELINE_STATUS_FAILED}:
        raise HTTPException(
            status_code=409,
            detail="Pipeline kann in diesem Status nicht abgebrochen werden",
        )

    refs = _pipeline_refs(job)
    refs["cancel_requested"] = True
    updated = save_pipeline_state(
        pipeline_id,
        status=PIPELINE_STATUS_CANCELLED,
        result_refs=refs,
        error=None,
    )

    transcription_job_id = job.get("transcription_job_id")
    if transcription_job_id:
        try:
            request_job_cancellation(transcription_job_id)
        except HTTPException as exc:
            if exc.status_code != 409:
                raise

    if refs.get("pdf_path"):
        remove_upload_file(refs.get("pdf_path"))

    return updated


# ----- Endpoints -----


@app.get("/")
async def root():
    return {"message": "Protokollierungsassistenz API", "version": "0.1.0"}


@app.get("/health")
async def health_check():
    """
    Health check endpoint for Docker/Kubernetes.
    Returns 200 only when models are loaded and server is ready.
    """
    if not getattr(app.state, "models_loaded", False):
        raise HTTPException(
            status_code=503, detail="Models not loaded yet - server starting up"
        )
    return {"status": "healthy", "models_loaded": True, "version": "0.1.0"}


@app.post("/api/pipeline/start", response_model=PipelineStartResponse)
async def start_pipeline(
    audio: UploadFile = File(...),
    pdf: Optional[UploadFile] = File(None),
    session_id: Optional[str] = Form(None),
    tops: Optional[str] = Form(None),
    options: Optional[str] = Form(None),
    model: Optional[str] = Form(None),
    system_prompt: Optional[str] = Form(None),
    remember_speakers: bool = Form(False),
):
    """Start an unattended upload-to-review pipeline job."""
    if (
        not getattr(app.state, "models_loaded", False)
        or getattr(app.state, "models", None) is None
    ):
        raise HTTPException(
            status_code=503,
            detail="Server startet noch - Modelle werden geladen. Bitte warten.",
        )
    if not is_allowed_audio_file(audio.filename, audio.content_type):
        raise HTTPException(
            status_code=400,
            detail="Ungültiger Dateityp. Erlaubt: MP3, WAV, M4A",
        )
    if pdf is not None and pdf.filename:
        if not is_allowed_pdf_file(pdf.filename, pdf.content_type):
            raise HTTPException(status_code=400, detail="Nur PDF-Dateien sind erlaubt")

    pipeline_id = str(uuid.uuid4())
    transcription_job_id = str(uuid.uuid4())
    effective_session_id = session_id or str(uuid.uuid4())
    parsed_options = parse_pipeline_options(options)
    if model:
        parsed_options["model"] = model
    if system_prompt:
        parsed_options["system_prompt"] = system_prompt
    known_tops = parse_pipeline_tops(tops)

    safe_audio_filename = normalize_upload_filename(
        audio.filename,
        default_stem="audio",
        allowed_extensions=ALLOWED_AUDIO_EXTENSIONS,
        content_type=audio.content_type,
    )
    audio_path = upload_path_for(transcription_job_id, safe_audio_filename)
    audio_size_bytes = await save_upload_with_size_limit(audio, audio_path)

    pdf_path: str | None = None
    if pdf is not None and pdf.filename:
        safe_pdf_filename = normalize_upload_filename(
            pdf.filename,
            default_stem="agenda",
            allowed_extensions=(".pdf",),
            content_type=pdf.content_type,
        )
        pdf_destination = UPLOAD_DIR / f"{pipeline_id}-{safe_pdf_filename}"
        await save_upload_with_size_limit(pdf, pdf_destination)
        pdf_path = str(pdf_destination)

    now = time.time()
    with JOB_LOCK:
        jobs[transcription_job_id] = {
            "created_at": now,
            "updated_at": now,
            "session_id": effective_session_id,
            "status": JOB_STATUS_PENDING,
            "progress": 0,
            "message": "Audio hochgeladen, Pipeline wartet auf Verarbeitung",
            "file_path": str(audio_path),
            "audio_path": str(audio_path),
            "audio_filename": safe_audio_filename,
            "audio_content_type": audio.content_type,
            "audio_size_bytes": audio_size_bytes,
            "remember_speakers": remember_speakers,
            "transcript": None,
            "error": None,
            "cancellation_requested": False,
        }
    persist_job_state(transcription_job_id)
    save_pipeline_session(
        effective_session_id,
        job_id=transcription_job_id,
        tops=known_tops,
        current_step=0,
    )

    pipeline_job = save_pipeline_job(
        pipeline_id,
        {
            "session_id": effective_session_id,
            "transcription_job_id": transcription_job_id,
            "status": PIPELINE_STATUS_PENDING,
            "stage": PIPELINE_STAGE_UPLOAD,
            "progress": 5,
            "error": None,
            "created_at": now,
            "updated_at": now,
            "result_refs": {
                "audio_path": str(audio_path),
                "pdf_path": pdf_path,
                "known_tops": known_tops,
                "options": parsed_options,
                "remember_speakers": remember_speakers,
                "warnings": [],
            },
        },
    )

    manager = await get_or_create_pipeline_manager()
    await manager.enqueue(pipeline_id)
    return PipelineStartResponse(
        pipeline_id=pipeline_id,
        session_id=effective_session_id,
        transcription_job_id=transcription_job_id,
        status=pipeline_job["status"],
        stage=pipeline_job["stage"],
        progress=pipeline_job["progress"],
        warnings=[],
    )


@app.get("/api/pipeline/{pipeline_id}", response_model=PipelineStatusResponse)
async def get_pipeline_status(pipeline_id: str):
    job = load_pipeline_job(pipeline_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Pipeline nicht gefunden")
    return build_pipeline_status_response(job)


@app.post("/api/pipeline/{pipeline_id}/cancel", response_model=PipelineStatusResponse)
async def cancel_pipeline(pipeline_id: str):
    job = request_pipeline_cancellation(pipeline_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Pipeline nicht gefunden")
    return build_pipeline_status_response(job)


@app.get("/api/pipeline/{pipeline_id}/result", response_model=PipelineResultResponse)
async def get_pipeline_result(pipeline_id: str):
    pipeline_job = load_pipeline_job(pipeline_id)
    if pipeline_job is None:
        raise HTTPException(status_code=404, detail="Pipeline nicht gefunden")
    if (
        pipeline_job.get("status") != PIPELINE_STATUS_COMPLETED
        or pipeline_job.get("stage") != PIPELINE_STAGE_READY_FOR_REVIEW
    ):
        raise HTTPException(status_code=409, detail="Pipeline ist noch nicht reviewbar")

    session_id = pipeline_job.get("session_id")
    if not session_id:
        raise HTTPException(status_code=404, detail="Pipeline hat keine Session")
    session = load_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session nicht gefunden")

    session_response = build_session_response(session)
    observations = [
        build_speaker_observation_response(observation, session)
        for observation in load_speaker_observations(session_id=session_id)
    ]
    status = build_pipeline_status_response(pipeline_job)
    return PipelineResultResponse(
        pipeline=status,
        session=session_response,
        job=session_response.job,
        speaker_observations=observations,
        summary_reviews=session_response.summary_reviews,
        warnings=status.warnings,
    )


@app.post("/api/sessions", response_model=SessionResponse)
async def create_or_save_session(request: SessionSaveRequest):
    """
    Create or save a persisted editing session.

    This stores user-editable state only: TOPs, corrected transcript lines,
    line assignments, speaker display names, summaries and the linked
    transcription job. Audio bytes are not copied.
    """
    session_id = request.session_id or str(uuid.uuid4())
    session = save_session(session_id, model_to_dict(request))
    return build_session_response(session)


@app.put("/api/sessions/{session_id}", response_model=SessionResponse)
async def save_existing_session(session_id: str, request: SessionSaveRequest):
    """Save a persisted editing session under a known session ID."""
    state = model_to_dict(request)
    state["session_id"] = session_id
    session = save_session(session_id, state)
    return build_session_response(session)


@app.get("/api/sessions/{session_id}", response_model=SessionResponse)
async def get_session(session_id: str):
    """Load a persisted editing session and its linked transcription job."""
    session = load_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session nicht gefunden")
    return build_session_response(session)


@app.get("/api/speaker-profiles", response_model=List[SpeakerProfileResponse])
async def list_speaker_profiles(
    scope: Optional[str] = None,
    include_archived: bool = False,
):
    """List global speaker profiles. Archived profiles are hidden by default."""
    return [
        build_speaker_profile_response(profile)
        for profile in load_speaker_profiles(
            scope=scope,
            include_archived=include_archived,
        )
    ]


@app.post("/api/speaker-profiles", response_model=SpeakerProfileResponse)
async def create_speaker_profile_endpoint(request: SpeakerProfileCreateRequest):
    """Create a speaker profile only after explicit user action."""
    profile = create_speaker_profile(
        validate_display_name(request.display_name),
        scope=request.scope,
    )
    return build_speaker_profile_response(profile)


@app.put("/api/speaker-profiles/{profile_id}", response_model=SpeakerProfileResponse)
async def update_speaker_profile_endpoint(
    profile_id: str,
    request: SpeakerProfileUpdateRequest,
):
    """Rename or rescope an active speaker profile."""
    existing = get_required_active_profile(profile_id)
    fields_set = (
        request.model_fields_set
        if hasattr(request, "model_fields_set")
        else request.__fields_set__
    )
    display_name = (
        validate_display_name(request.display_name)
        if request.display_name is not None
        else existing["display_name"]
    )
    scope = request.scope if "scope" in fields_set else existing.get("scope")
    updated = update_speaker_profile(
        profile_id,
        display_name=display_name,
        scope=scope,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Profil nicht gefunden")
    if updated.get("archived_at") is not None:
        raise HTTPException(status_code=409, detail="Profil ist archiviert")
    return build_speaker_profile_response(updated)


@app.delete("/api/speaker-profiles/{profile_id}", response_model=SpeakerProfileResponse)
async def archive_speaker_profile_endpoint(profile_id: str):
    """Archive a speaker profile instead of hard-deleting it."""
    get_required_active_profile(profile_id)
    archived = archive_speaker_profile(profile_id)
    if archived is None:
        raise HTTPException(status_code=404, detail="Profil nicht gefunden")
    anonymize_speaker_observations_for_profile(profile_id)
    return build_speaker_profile_response(archived)


@app.delete("/api/speaker-profiles/{profile_id}/embeddings")
async def delete_speaker_profile_embeddings_endpoint(profile_id: str):
    """Delete persisted biometric embeddings for a speaker profile."""
    profile = load_speaker_profile(profile_id, include_archived=True)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profil nicht gefunden")
    deleted_count = delete_speaker_embeddings(profile_id)
    return {"profile_id": profile_id, "deleted_count": deleted_count}


@app.get(
    "/api/sessions/{session_id}/speaker-observations",
    response_model=List[SpeakerObservationResponse],
)
async def list_session_speaker_observations(session_id: str):
    """Return reviewable speaker observations for a session."""
    session = get_required_session(session_id)
    observations = load_speaker_observations(session_id=session_id)
    return [
        build_speaker_observation_response(observation, session)
        for observation in observations
    ]


@app.post(
    "/api/sessions/{session_id}/speaker-observations/{observation_id}/confirm",
    response_model=SpeakerObservationResponse,
)
async def confirm_session_speaker_observation(
    session_id: str,
    observation_id: int,
    request: SpeakerObservationConfirmRequest | None = None,
):
    """Confirm a suggested speaker-profile match after user review."""
    session = get_required_session(session_id)
    observation = get_observation_for_session(session_id, observation_id)
    if observation.get("status") == "rejected":
        raise HTTPException(
            status_code=409,
            detail="Abgelehnte Observation kann nicht bestätigt werden",
        )
    ensure_local_speaker_exists(session, observation["local_speaker_id"])
    ensure_no_accepted_local_mapping(
        session_id,
        observation["local_speaker_id"],
        exclude_observation_id=observation_id,
    )

    requested_profile_id = request.profile_id if request else None
    profile_id = requested_profile_id or observation.get("profile_id")
    if profile_id is None:
        raise HTTPException(status_code=400, detail="Profil fehlt für Bestätigung")
    profile = get_required_active_profile(profile_id)

    confirmed = confirm_speaker_observation(
        observation_id,
        profile_id=profile_id,
        confidence=request.confidence if request else None,
    )
    if confirmed is None:
        raise HTTPException(status_code=404, detail="Observation nicht gefunden")
    updated_session = apply_profile_display_name_to_session(
        session,
        observation["local_speaker_id"],
        profile,
    )
    add_job_embedding_to_profile(
        job_id=observation["job_id"],
        local_speaker_id=observation["local_speaker_id"],
        profile_id=profile_id,
        observation_id=observation_id,
        storage_reason="confirm",
    )
    return build_speaker_observation_response(confirmed, updated_session)


@app.post(
    "/api/sessions/{session_id}/speaker-observations/{observation_id}/reject",
    response_model=SpeakerObservationResponse,
)
async def reject_session_speaker_observation(session_id: str, observation_id: int):
    """Reject a suggested speaker-profile match after user review."""
    session = get_required_session(session_id)
    observation = get_observation_for_session(session_id, observation_id)
    if observation.get("status") in {"confirmed", "manual"}:
        raise HTTPException(
            status_code=409,
            detail="Bestätigte Zuordnung kann nicht abgelehnt werden",
        )
    rejected = reject_speaker_observation(observation_id)
    if rejected is None:
        raise HTTPException(status_code=404, detail="Observation nicht gefunden")
    return build_speaker_observation_response(rejected, session)


@app.post(
    "/api/sessions/{session_id}/speaker-observations/manual",
    response_model=SpeakerObservationResponse,
)
async def create_manual_speaker_observation(
    session_id: str,
    request: SpeakerObservationManualRequest,
):
    """Manually link a local speaker to an active or newly created profile."""
    session = get_required_session(session_id)
    local_speaker_id = request.local_speaker_id.strip()
    ensure_local_speaker_exists(session, local_speaker_id)
    ensure_no_accepted_local_mapping(session_id, local_speaker_id)

    if bool(request.profile_id) == bool(request.display_name):
        raise HTTPException(
            status_code=400,
            detail="Genau eines von profile_id oder display_name ist erforderlich",
        )

    profile = (
        get_required_active_profile(request.profile_id)
        if request.profile_id
        else create_speaker_profile(
            validate_display_name(request.display_name or ""),
            scope=request.scope,
        )
    )

    observation_id = request.observation_id
    if observation_id is not None:
        existing = get_observation_for_session(session_id, observation_id)
        if existing["local_speaker_id"] != local_speaker_id:
            raise HTTPException(
                status_code=409,
                detail="Observation gehört zu einem anderen lokalen Sprecher",
            )
        job_id = existing["job_id"]
    else:
        job_id = session.get("job_id")
        if not job_id or load_job(job_id) is None:
            raise HTTPException(
                status_code=409,
                detail="Session hat keinen gültigen Transkriptionsjob",
            )
        for existing in load_speaker_observations(session_id=session_id):
            if (
                existing["local_speaker_id"] == local_speaker_id
                and existing.get("profile_id") == profile["profile_id"]
                and existing.get("status") == "suggested"
            ):
                observation_id = existing["observation_id"]
                job_id = existing["job_id"]
                break

    manual = save_speaker_observation(
        job_id=job_id,
        session_id=session_id,
        local_speaker_id=local_speaker_id,
        profile_id=profile["profile_id"],
        confidence=request.confidence,
        status="manual",
        observation_id=observation_id,
    )
    updated_session = apply_profile_display_name_to_session(
        session,
        local_speaker_id,
        profile,
    )
    add_job_embedding_to_profile(
        job_id=job_id,
        local_speaker_id=local_speaker_id,
        profile_id=profile["profile_id"],
        observation_id=manual["observation_id"],
        storage_reason="manual",
    )
    return build_speaker_observation_response(manual, updated_session)


@app.post("/api/export")
async def export_protocol_endpoint(request: ProtocolExportRequest):
    """Render the completed protocol as TXT, DOCX or PDF."""
    export_format = request.format.lower().strip()
    if export_format not in {"txt", "docx", "pdf"}:
        raise HTTPException(status_code=400, detail="Exportformat nicht unterstützt")
    if not request.tops:
        raise HTTPException(status_code=400, detail="Keine TOPs vorhanden")

    filtered_tops = [top.strip() for top in request.tops if top.strip()]
    if not filtered_tops:
        raise HTTPException(status_code=400, detail="Keine TOPs vorhanden")

    metadata = ProtocolMetadata(
        committee=request.metadata.committee.strip(),
        date=request.metadata.date.strip(),
        location=request.metadata.location.strip(),
        title=request.metadata.title.strip() or "Sitzungsprotokoll",
        participants=[
            participant.strip()
            for participant in request.metadata.participants
            if participant.strip()
        ],
    )
    appendix = ProtocolAppendix(
        include_speaker_list=request.appendix.include_speaker_list,
        include_transcript_excerpt=request.appendix.include_transcript_excerpt,
        include_generation_note=request.appendix.include_generation_note,
        transcript_excerpt_limit=request.appendix.transcript_excerpt_limit,
    )
    export_transcript = [
        ExportTranscriptLine(
            speaker=request.speaker_names.get(line.speaker, line.speaker),
            text=line.text,
            start=line.start,
            end=line.end,
        )
        for line in request.transcript
    ]
    document = build_protocol_document(
        metadata=metadata,
        tops=filtered_tops,
        summaries=request.summaries,
        summary_reviews=request.summary_reviews,
        transcript=export_transcript,
        speaker_names=request.speaker_names,
        appendix=appendix,
    )
    content = render_protocol(document, export_format)  # type: ignore[arg-type]
    media_types = {
        "txt": "text/plain; charset=utf-8",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "pdf": "application/pdf",
    }
    title_stem = normalize_upload_filename(
        metadata.title or "protokoll",
        default_stem="protokoll",
        allowed_extensions=(),
    ).removesuffix(".")
    filename = f"{title_stem or 'protokoll'}.{export_format}"
    return Response(
        content=content,
        media_type=media_types[export_format],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/transcribe", response_model=TranscriptionJob)
async def start_transcription(
    audio: UploadFile = File(...),
    session_id: Optional[str] = Form(None),
    remember_speakers: bool = Form(False),
):
    """
    Upload audio file and start transcription job.
    Returns job_id to poll for status.
    """
    logger.info(
        f"Received transcription request: {audio.filename} ({audio.content_type})"
    )

    # Check if models are loaded
    if (
        not getattr(app.state, "models_loaded", False)
        or getattr(app.state, "models", None) is None
    ):
        logger.error("Transcription request rejected - models not loaded")
        raise HTTPException(
            status_code=503,
            detail="Server startet noch - Modelle werden geladen. Bitte warten.",
        )

    # Validate file type
    if not is_allowed_audio_file(audio.filename, audio.content_type):
        logger.warning(f"Rejected file with invalid type: {audio.content_type}")
        raise HTTPException(
            status_code=400, detail=f"Ungültiger Dateityp. Erlaubt: MP3, WAV, M4A"
        )

    job_id = str(uuid.uuid4())
    logger.info(f"Created job: {job_id}")

    safe_filename = normalize_upload_filename(
        audio.filename,
        default_stem="audio",
        allowed_extensions=ALLOWED_AUDIO_EXTENSIONS,
        content_type=audio.content_type,
    )
    file_path = upload_path_for(job_id, safe_filename)
    size_bytes = await save_upload_with_size_limit(audio, file_path)
    logger.info(f"Saved file: {file_path} ({size_bytes} bytes)")

    with JOB_LOCK:
        jobs[job_id] = {
            "created_at": time.time(),
            "updated_at": time.time(),
            "session_id": session_id,
            "status": JOB_STATUS_PENDING,
            "progress": 0,
            "message": "Audio hochgeladen, Job wartet auf Verarbeitung",
            "file_path": str(file_path),
            "audio_path": str(file_path),
            "audio_filename": safe_filename,
            "audio_content_type": audio.content_type,
            "audio_size_bytes": size_bytes,
            "remember_speakers": remember_speakers,
            "transcript": None,
            "error": None,
            "cancellation_requested": False,
        }
    persist_job_state(job_id)

    # Cleanup old jobs to prevent memory buildup
    cleanup_old_jobs()

    manager = await get_or_create_job_manager()
    await manager.enqueue(job_id)
    logger.info(f"Queued transcription job: {job_id}")

    return TranscriptionJob(
        job_id=job_id,
        status=JOB_STATUS_PENDING,
        progress=0,
        message="Transkription gestartet",
    )


@app.get("/api/transcribe/{job_id}", response_model=TranscriptionJob)
async def get_transcription_status(job_id: str):
    """
    Get status of transcription job.
    """
    job = get_job_from_cache_or_db(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job nicht gefunden")

    return build_transcription_job_response(job_id, job)


@app.post("/api/transcribe/{job_id}/cancel", response_model=TranscriptionJob)
async def cancel_transcription(job_id: str):
    """Cancel a pending job or request cooperative cancellation for an active job."""
    job = request_job_cancellation(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job nicht gefunden")
    return build_transcription_job_response(job_id, job)


@app.get("/api/audio/{job_id}")
async def stream_audio(
    job_id: str,
    range: Optional[str] = Header(None, alias="Range"),
):
    """
    Stream audio file for a transcription job.
    Supports HTTP Range requests for efficient seeking.
    """
    job = get_job_from_cache_or_db(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job nicht gefunden")

    audio_path = get_audio_path_for_job(job)

    if not audio_path or not os.path.exists(audio_path):
        raise HTTPException(status_code=404, detail="Audio nicht mehr verfügbar")

    file_size = os.path.getsize(audio_path)

    # Determine content type
    content_type, _ = mimetypes.guess_type(audio_path)
    if not content_type:
        content_type = "audio/mpeg"

    # Handle Range requests for seeking
    if range:
        # Parse range header: "bytes=start-end"
        range_match = re.match(r"bytes=(\d+)-(\d*)", range)
        if range_match:
            start = int(range_match.group(1))
            end = int(range_match.group(2)) if range_match.group(2) else file_size - 1

            if start >= file_size:
                raise HTTPException(status_code=416, detail="Range Not Satisfiable")

            chunk_size = end - start + 1

            with open(audio_path, "rb") as f:
                f.seek(start)
                data = f.read(chunk_size)

            return Response(
                content=data,
                status_code=206,
                headers={
                    "Content-Range": f"bytes {start}-{end}/{file_size}",
                    "Accept-Ranges": "bytes",
                    "Content-Length": str(chunk_size),
                    "Content-Type": content_type,
                },
            )

    # Return full file if no range requested
    with open(audio_path, "rb") as f:
        data = f.read()

    return Response(
        content=data,
        status_code=200,
        headers={
            "Accept-Ranges": "bytes",
            "Content-Length": str(file_size),
            "Content-Type": content_type,
        },
    )


@app.post("/api/summarize", response_model=SummarizeResponse)
async def generate_summary(request: SummarizeRequest):
    """
    Generate summary for a TOP segment.
    """
    if not request.lines:
        raise HTTPException(status_code=400, detail="Keine Zeilen zum Zusammenfassen")

    # Combine lines into text
    text = "\n".join([f"{line.speaker}: {line.text}" for line in request.lines])

    try:
        result = summarize_segment(
            request.top_title,
            text,
            model=request.model,
            system_prompt=request.system_prompt,
        )
        review = build_summary_review(
            structured=result.structured,
            summary=result.summary,
            lines=request.lines,
        )
        return SummarizeResponse(
            summary=result.summary,
            duration_seconds=result.duration_seconds,
            structured=(
                StructuredSummaryResponse(**result.structured.to_dict())
                if result.structured
                else None
            ),
            source_links=[
                SummarySourceLinkResponse(**link.to_dict())
                for link in review.source_links
            ],
            review_warnings=[
                SummaryReviewWarningResponse(**warning.to_dict())
                for warning in review.warnings
            ],
            fallback_used=result.fallback_used,
            chunks_processed=result.chunks_processed,
        )
    except LLMCallError as e:
        status_code = 504 if e.category == "timeout" else 503 if e.transient else 500
        raise HTTPException(
            status_code=status_code,
            detail=f"Fehler bei der Zusammenfassung ({e.category}): {str(e)}",
        )
    except StructuredOutputError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Fehler bei der strukturierten Zusammenfassung: {str(e)}",
        )
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Fehler bei der Zusammenfassung: {str(e)}"
        )


@app.post("/api/extract-tops", response_model=ExtractTOPsResponse)
async def extract_tops_endpoint(
    pdf: UploadFile = File(...),
    model: Optional[str] = Form(None),
    system_prompt: Optional[str] = Form(None),
):
    """
    Extract TOPs (agenda items) from a German municipal meeting invitation PDF.
    Uses LLM to intelligently parse the document structure.
    """
    logger.info(f"Received PDF for TOP extraction: {pdf.filename} ({pdf.content_type})")

    # Validate file type
    if not is_allowed_pdf_file(pdf.filename, pdf.content_type):
        logger.warning(f"Rejected non-PDF file: {pdf.content_type}")
        raise HTTPException(status_code=400, detail="Nur PDF-Dateien sind erlaubt")

    # Save uploaded file temporarily
    file_id = str(uuid.uuid4())
    safe_filename = normalize_upload_filename(
        pdf.filename,
        default_stem="document",
        allowed_extensions=(".pdf",),
        content_type=pdf.content_type,
    )
    file_path = upload_path_for(file_id, safe_filename)

    try:
        size_bytes = await save_upload_with_size_limit(pdf, file_path)
        logger.info(f"Saved PDF: {file_path} ({size_bytes} bytes)")

        # Extract TOPs using LLM
        tops = extract_tops_from_pdf(
            str(file_path),
            model=model,
            system_prompt=system_prompt,
        )

        logger.info(f"Successfully extracted {len(tops)} TOPs from {pdf.filename}")
        return ExtractTOPsResponse(tops=tops)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"TOP extraction failed: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Fehler bei der TOP-Extraktion: {str(e)}"
        )

    finally:
        # Clean up uploaded file
        try:
            if file_path.exists():
                os.remove(file_path)
                logger.info(f"Cleaned up PDF file: {file_path}")
        except Exception as cleanup_error:
            logger.warning(f"Failed to clean up PDF: {cleanup_error}")


@app.post("/api/assignment-suggestions", response_model=AssignmentSuggestionsResponse)
async def assignment_suggestions_endpoint(request: AssignmentSuggestionsRequest):
    """
    Suggest transcript-to-TOP assignments with explainable heuristic boundaries.
    Users still review and accept/correct the suggestions in the frontend.
    """
    if not request.transcript:
        raise HTTPException(status_code=400, detail="Kein Transkript vorhanden")
    if not any(top.strip() for top in request.tops):
        raise HTTPException(status_code=400, detail="Keine TOPs vorhanden")

    transcript = [
        TranscriptUtterance(speaker=line.speaker, text=line.text)
        for line in request.transcript
    ]
    result = suggest_assignments(transcript, request.tops)

    return AssignmentSuggestionsResponse(
        suggested_assignments=result.suggested_assignments,
        segments=[
            AssignmentSuggestionSegmentResponse(
                top_index=segment.top_index,
                top_title=segment.top_title,
                start_index=segment.start_index,
                end_index=segment.end_index,
                confidence=segment.confidence,
                uncertain=segment.uncertain,
                transition_type=segment.transition_type,
                reason=segment.reason,
                evidence_index=segment.evidence_index,
                evidence_text=segment.evidence_text,
            )
            for segment in result.segments
        ],
        strategy=result.strategy,
        uncertain_count=result.uncertain_count,
    )


@app.post("/api/agenda-detection", response_model=AgendaDetectionResponse)
async def agenda_detection_endpoint(request: AgendaDetectionRequest):
    """
    Detect reviewable TOPs and transcript segments.

    If TOPs are supplied, only boundaries are detected/refined. Without TOPs,
    the endpoint detects agenda titles from transcript transition signals and
    optionally an LLM structured-output pass.
    """
    if not request.transcript:
        raise HTTPException(status_code=400, detail="Kein Transkript vorhanden")

    transcript = [
        TranscriptUtterance(speaker=line.speaker, text=line.text)
        for line in request.transcript
    ]
    valid_tops = [top.strip() for top in request.tops if top.strip()]
    if valid_tops:
        result = segment_known_agenda(
            transcript,
            valid_tops,
            model=request.model,
            system_prompt=request.system_prompt,
        )
    else:
        result = detect_agenda_from_transcript(
            transcript,
            model=request.model,
            system_prompt=request.system_prompt,
        )

    return AgendaDetectionResponse(
        tops=result.tops,
        assignments=result.assignments,
        segments=[
            AssignmentSuggestionSegmentResponse(
                top_index=segment.top_index,
                top_title=segment.top_title,
                start_index=segment.start_index,
                end_index=segment.end_index,
                confidence=segment.confidence,
                uncertain=segment.uncertain,
                transition_type=segment.transition_type,
                reason=segment.reason,
                evidence_index=segment.evidence_index,
                evidence_text=segment.evidence_text,
            )
            for segment in result.segments
        ],
        uncertain_count=result.uncertain_count,
        strategy=result.strategy,
    )


@app.post("/api/telemetry/session-complete", response_model=SessionCompleteResponse)
async def report_session_complete(request: SessionCompleteRequest):
    """
    Report session completion and send telemetry data.

    Called by the frontend when the user exports the protocol.
    Combines transcription metrics (stored in job) with summarization metrics (from frontend).
    """
    if not request.telemetry_consent:
        logger.info("Telemetry report ignored because consent was not provided")
        return SessionCompleteResponse(
            success=True,
            message="Telemetry disabled by user",
        )

    logger.info(f"Received session complete report for job: {request.job_id}")

    # Get job data
    job = get_job_from_cache_or_db(request.job_id)
    if not job:
        logger.warning(f"Job {request.job_id} not found for telemetry")
        # Still send telemetry with available data
        collector = TelemetryCollector()
        collector.set_summarization_metrics(
            llm_model=request.llm_model,
            system_prompt_kind=request.system_prompt_kind,
            top_count=request.top_count,
            summarization_duration_seconds=request.summarization_duration_seconds,
            protocol_char_count=request.protocol_char_count,
        )
        collector.send()
        return SessionCompleteResponse(
            success=True,
            message="Telemetry sent (job not found, partial data)",
        )

    # Create telemetry collector and populate with all data
    collector = TelemetryCollector()

    # Set Whisper config
    telemetry_data = job.get("telemetry", {})
    if telemetry_data:
        collector.set_whisper_config(
            model=telemetry_data.get("whisper_model", WHISPER_MODEL),
            batch_size=telemetry_data.get("whisper_batch_size", WHISPER_BATCH_SIZE),
        )

        # Set transcription metrics
        collector.set_transcription_metrics(
            audio_duration_seconds=telemetry_data.get("audio_duration_seconds", 0),
            transcription_duration_seconds=telemetry_data.get(
                "transcription_duration_seconds", 0
            ),
            transcript_line_count=telemetry_data.get("transcript_line_count", 0),
            transcript_char_count=telemetry_data.get("transcript_char_count", 0),
        )

    # Set summarization metrics from frontend
    collector.set_summarization_metrics(
        llm_model=request.llm_model,
        system_prompt_kind=request.system_prompt_kind,
        top_count=request.top_count,
        summarization_duration_seconds=request.summarization_duration_seconds,
        protocol_char_count=request.protocol_char_count,
    )

    # Send telemetry
    collector.send()

    logger.info(f"Telemetry sent for job {request.job_id}")
    return SessionCompleteResponse(
        success=True,
        message="Telemetry sent successfully",
    )


# ----- Transcription Worker -----


def run_transcription(
    job_id: str,
    file_path: str | None,
    models: TranscriptionModels,
) -> None:
    """
    Run transcription in a managed worker using pre-loaded models.

    Transcription itself is not retried automatically: long GPU jobs can be
    expensive and failure modes are often input/model related. Cancellation is
    cooperative via the progress callback and checked again before persisting
    a completed result.
    """
    logger.info(f"[Job {job_id}] Worker task started")
    try:
        if get_job_from_cache_or_db(job_id) is None:
            persisted_job = load_job(job_id)
            if persisted_job:
                with JOB_LOCK:
                    jobs[job_id] = persisted_job
        job = get_job_from_cache_or_db(job_id)
        if job is None:
            raise RuntimeError("Job nicht gefunden")
        if is_job_cancelled(job_id):
            raise CancellationRequested()
        if not file_path:
            raise RuntimeError("Upload-Datei nicht gefunden")
        if not os.path.exists(file_path):
            raise RuntimeError("Upload-Datei ist nicht mehr verfügbar")

        update_job_state(
            job_id,
            status=JOB_STATUS_PROCESSING,
            progress=10,
            message="Transkription wird vorbereitet...",
        )
        logger.info(f"[Job {job_id}] Status: processing, preparing transcription...")

        # Run transcription with pre-loaded models
        def progress_callback(progress: int, message: str):
            if is_job_cancelled(job_id):
                raise CancellationRequested()
            update_job_state(
                job_id,
                progress=progress,
                message=message,
            )
            logger.info(f"[Job {job_id}] Progress: {progress}% - {message}")

        # Time the transcription
        transcription_start = time.time()
        result = transcribe_audio(file_path, models, progress_callback)
        transcription_duration = time.time() - transcription_start

        if is_job_cancelled(job_id):
            raise CancellationRequested()

        transcript = result.transcript

        # Calculate transcript metrics
        transcript_line_count = len(transcript)
        transcript_char_count = sum(len(line.get("text", "")) for line in transcript)
        speaker_embeddings = getattr(result, "speaker_embeddings", None) or []

        try:
            persist_job_speaker_embeddings(job_id, speaker_embeddings)
            create_speaker_suggestion_observations(
                job_id=job_id,
                session_id=job.get("session_id"),
                local_embeddings=speaker_embeddings,
                speaker_memory_opt_in=bool(job.get("remember_speakers")),
            )
        except Exception as e:
            logger.warning(
                "[Job %s] Speaker embedding matching failed without failing transcription: %s",
                job_id,
                e,
                exc_info=True,
            )

        update_job_state(
            job_id,
            status=JOB_STATUS_COMPLETED,
            progress=100,
            message="Transkription abgeschlossen",
            transcript=transcript,
            audio_path=file_path,
            error=None,
            telemetry={
                "audio_duration_seconds": result.audio_duration_seconds,
                "transcription_duration_seconds": transcription_duration,
                "transcript_line_count": transcript_line_count,
                "transcript_char_count": transcript_char_count,
                "whisper_model": WHISPER_MODEL,
                "whisper_batch_size": WHISPER_BATCH_SIZE,
            },
        )

        logger.info(
            f"[Job {job_id}] Transcription completed successfully with {transcript_line_count} lines"
        )

    except CancellationRequested:
        logger.info(f"[Job {job_id}] Transcription cancelled")
        job = update_job_state(
            job_id,
            status=JOB_STATUS_CANCELLED,
            message="Transkription abgebrochen",
            error=None,
            cancellation_requested=True,
        )
        if DELETE_UPLOADS_ON_CANCEL_OR_FAILURE and job:
            cleanup_job_uploads(job_id, job)
    except Exception as e:
        logger.error(f"[Job {job_id}] Transcription failed: {str(e)}", exc_info=True)
        job = update_job_state(
            job_id,
            status=JOB_STATUS_FAILED,
            error=str(e),
            message=f"Fehler: {str(e)}",
        )
        if DELETE_UPLOADS_ON_CANCEL_OR_FAILURE and job:
            cleanup_job_uploads(job_id, job)
    finally:
        # Clean up GPU memory after every terminal worker run.
        try:
            import gc
            import torch

            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                logger.info(f"[Job {job_id}] GPU memory cleared")
        except Exception:
            pass


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8010)
