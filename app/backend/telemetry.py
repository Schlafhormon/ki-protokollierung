"""
Telemetry module for collecting opt-in usage metrics.

Telemetry must be explicitly enabled by the user in the frontend before this
module is called. Payloads only contain operational metadata and aggregate
counts; transcript, audio, person, protocol and prompt contents are excluded.

Configuration:
- TELEMETRY_WEBHOOK_URL: webhook URL (required for sending)
- TELEMETRY_BACKUP_ENABLED: save local jsonl backup files (default: false)
- TELEMETRY_BACKUP_RETENTION_DAYS: delete older backup files (default: 14)
- TELEMETRY_BACKUP_MAX_FILES: keep at most this many backup files (default: 30)
- APP_VERSION: Application version string (default: 0.1.0)
"""

import os
import json
import logging
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)

# Configuration
TELEMETRY_WEBHOOK_URL = os.environ.get("TELEMETRY_WEBHOOK_URL", "")
APP_VERSION = os.environ.get("APP_VERSION", "0.1.0")
TELEMETRY_BACKUP_DIR = Path(os.environ.get("TELEMETRY_BACKUP_DIR", "telemetry_backup"))
TELEMETRY_BACKUP_ENABLED = (
    os.environ.get("TELEMETRY_BACKUP_ENABLED", "false").lower() == "true"
)
TELEMETRY_BACKUP_RETENTION_DAYS = int(
    os.environ.get("TELEMETRY_BACKUP_RETENTION_DAYS", "14")
)
TELEMETRY_BACKUP_MAX_FILES = int(os.environ.get("TELEMETRY_BACKUP_MAX_FILES", "30"))


@dataclass
class TelemetryEvent:
    """Container for all telemetry metrics collected during a session."""

    # Timestamps
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    app_version: str = field(default_factory=lambda: APP_VERSION)

    # Hardware info
    gpu_name: Optional[str] = None
    gpu_vram_gb: Optional[float] = None
    device_type: str = "cpu"

    # Whisper configuration
    whisper_model: Optional[str] = None
    whisper_batch_size: Optional[int] = None

    # Transcription metrics
    audio_duration_seconds: Optional[float] = None
    transcription_duration_seconds: Optional[float] = None
    transcript_line_count: Optional[int] = None
    transcript_char_count: Optional[int] = None

    # LLM/Summarization metrics
    llm_model: Optional[str] = None
    system_prompt_kind: Optional[str] = None

    # Session metrics (from frontend)
    top_count: Optional[int] = None
    summarization_duration_seconds: Optional[float] = None
    protocol_char_count: Optional[int] = None

    # Status
    success: bool = True
    error_type: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


def get_gpu_info() -> tuple[Optional[str], Optional[float], str]:
    """
    Get GPU information using PyTorch.

    Returns:
        Tuple of (gpu_name, vram_gb, device_type)
    """
    try:
        import torch

        if torch.cuda.is_available():
            gpu_name = torch.cuda.get_device_name(0)
            vram_bytes = torch.cuda.get_device_properties(0).total_memory
            vram_gb = round(vram_bytes / (1024**3), 1)
            return gpu_name, vram_gb, "cuda"
        else:
            return None, None, "cpu"
    except Exception as e:
        logger.warning(f"Failed to get GPU info: {e}")
        return None, None, "cpu"


def _save_backup(event: TelemetryEvent) -> None:
    """Save telemetry event to local backup file."""
    if not TELEMETRY_BACKUP_ENABLED:
        return

    try:
        TELEMETRY_BACKUP_DIR.mkdir(exist_ok=True)
        _cleanup_backups()
        backup_file = TELEMETRY_BACKUP_DIR / f"telemetry_{datetime.now().strftime('%Y%m%d')}.jsonl"

        with open(backup_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")

        logger.info(f"Telemetry backup saved to {backup_file}")
    except Exception as e:
        logger.warning(f"Failed to save telemetry backup: {e}")


def _cleanup_backups() -> None:
    """Apply retention limits to local telemetry backups."""
    if not TELEMETRY_BACKUP_DIR.exists():
        return

    backup_files = sorted(
        TELEMETRY_BACKUP_DIR.glob("telemetry_*.jsonl"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    cutoff = datetime.now(timezone.utc) - timedelta(days=TELEMETRY_BACKUP_RETENTION_DAYS)

    for index, backup_file in enumerate(backup_files):
        modified = datetime.fromtimestamp(backup_file.stat().st_mtime, tz=timezone.utc)
        exceeds_age = modified < cutoff
        exceeds_count = index >= TELEMETRY_BACKUP_MAX_FILES
        if exceeds_age or exceeds_count:
            try:
                backup_file.unlink()
            except OSError as e:
                logger.warning(f"Failed to remove old telemetry backup {backup_file}: {e}")


def _send_to_webhook(event: TelemetryEvent) -> bool:
    """
    Send telemetry event to Google Sheets webhook.

    Returns:
        True if successful, False otherwise
    """
    if not TELEMETRY_WEBHOOK_URL:
        logger.debug("Telemetry webhook URL not configured, skipping send")
        return False

    try:
        data = json.dumps(event.to_dict(), ensure_ascii=False).encode("utf-8")

        request = urllib.request.Request(
            TELEMETRY_WEBHOOK_URL,
            data=data,
            headers={
                "Content-Type": "application/json",
            },
            method="POST",
        )

        with urllib.request.urlopen(request, timeout=10) as response:
            if response.status == 200:
                logger.info("Telemetry sent successfully")
                return True
            else:
                logger.warning(f"Telemetry webhook returned status {response.status}")
                return False

    except urllib.error.URLError as e:
        logger.warning(f"Failed to send telemetry (network error): {e}")
        return False
    except Exception as e:
        logger.warning(f"Failed to send telemetry: {e}")
        return False


def send_telemetry(event: TelemetryEvent) -> None:
    """
    Send telemetry event asynchronously.

    Optionally saves a retention-limited local backup, then attempts to send to webhook.
    Runs in background thread to avoid blocking the main request.
    """
    def _send():
        # Local backups are optional and retention-limited.
        _save_backup(event)

        # Attempt to send to webhook
        _send_to_webhook(event)

    # Run in background thread
    thread = threading.Thread(target=_send, daemon=True)
    thread.start()


class TelemetryCollector:
    """
    Collector for aggregating telemetry data during a session.

    Usage:
        collector = TelemetryCollector()
        collector.set_transcription_metrics(...)
        collector.set_summarization_metrics(...)
        collector.send()
    """

    def __init__(self):
        self.event = TelemetryEvent()
        self._set_hardware_info()

    def _set_hardware_info(self) -> None:
        """Populate hardware info from system."""
        gpu_name, vram_gb, device_type = get_gpu_info()
        self.event.gpu_name = gpu_name
        self.event.gpu_vram_gb = vram_gb
        self.event.device_type = device_type

    def set_whisper_config(self, model: str, batch_size: int) -> None:
        """Set Whisper model configuration."""
        self.event.whisper_model = model
        self.event.whisper_batch_size = batch_size

    def set_transcription_metrics(
        self,
        audio_duration_seconds: float,
        transcription_duration_seconds: float,
        transcript_line_count: int,
        transcript_char_count: int,
    ) -> None:
        """Set metrics from transcription phase."""
        self.event.audio_duration_seconds = audio_duration_seconds
        self.event.transcription_duration_seconds = transcription_duration_seconds
        self.event.transcript_line_count = transcript_line_count
        self.event.transcript_char_count = transcript_char_count

    def set_summarization_metrics(
        self,
        llm_model: str,
        system_prompt_kind: str,
        top_count: int,
        summarization_duration_seconds: float,
        protocol_char_count: int,
    ) -> None:
        """Set metrics from summarization phase (called from frontend)."""
        self.event.llm_model = llm_model
        self.event.system_prompt_kind = system_prompt_kind
        self.event.top_count = top_count
        self.event.summarization_duration_seconds = summarization_duration_seconds
        self.event.protocol_char_count = protocol_char_count

    def set_error(self, error_type: str) -> None:
        """Mark session as failed without storing free-form error contents."""
        self.event.success = False
        self.event.error_type = error_type

    def send(self) -> None:
        """Send collected telemetry."""
        send_telemetry(self.event)

    def to_dict(self) -> dict:
        """Get current telemetry data as dictionary."""
        return self.event.to_dict()
