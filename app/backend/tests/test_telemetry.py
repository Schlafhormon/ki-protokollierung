import os
import time
from pathlib import Path

from fastapi.testclient import TestClient

import main
import telemetry


class FakeTelemetryCollector:
    def __init__(self):
        self.whisper_config = None
        self.transcription_metrics = None
        self.summarization_metrics = None
        self.sent = False

    def set_whisper_config(self, model: str, batch_size: int) -> None:
        self.whisper_config = {
            "model": model,
            "batch_size": batch_size,
        }

    def set_transcription_metrics(
        self,
        audio_duration_seconds: float,
        transcription_duration_seconds: float,
        transcript_line_count: int,
        transcript_char_count: int,
    ) -> None:
        self.transcription_metrics = {
            "audio_duration_seconds": audio_duration_seconds,
            "transcription_duration_seconds": transcription_duration_seconds,
            "transcript_line_count": transcript_line_count,
            "transcript_char_count": transcript_char_count,
        }

    def set_summarization_metrics(
        self,
        llm_model: str,
        system_prompt_kind: str,
        top_count: int,
        summarization_duration_seconds: float,
        protocol_char_count: int,
    ) -> None:
        self.summarization_metrics = {
            "llm_model": llm_model,
            "system_prompt_kind": system_prompt_kind,
            "top_count": top_count,
            "summarization_duration_seconds": summarization_duration_seconds,
            "protocol_char_count": protocol_char_count,
        }

    def send(self) -> None:
        self.sent = True


def telemetry_request(consent: bool = True) -> dict:
    return {
        "telemetry_consent": consent,
        "job_id": "job-1",
        "top_count": 2,
        "protocol_char_count": 120,
        "summarization_duration_seconds": 3.5,
        "llm_model": "qwen3:8b",
        "system_prompt_kind": "custom",
    }


def test_session_complete_ignores_requests_without_opt_in(monkeypatch):
    created_collectors = []

    def collector_factory():
        collector = FakeTelemetryCollector()
        created_collectors.append(collector)
        return collector

    monkeypatch.setattr(main, "TelemetryCollector", collector_factory)

    with TestClient(main.app) as client:
        response = client.post(
            "/api/telemetry/session-complete",
            json=telemetry_request(consent=False),
        )

    assert response.status_code == 200
    assert response.json()["message"] == "Telemetry disabled by user"
    assert created_collectors == []


def test_session_complete_sends_only_aggregate_payload_after_opt_in(monkeypatch):
    collector = FakeTelemetryCollector()
    monkeypatch.setattr(main, "TelemetryCollector", lambda: collector)
    monkeypatch.setattr(
        main,
        "get_job_from_cache_or_db",
        lambda job_id: {
            "telemetry": {
                "audio_duration_seconds": 61.2,
                "transcription_duration_seconds": 12.4,
                "transcript_line_count": 9,
                "transcript_char_count": 300,
                "whisper_model": "test-whisper",
                "whisper_batch_size": 1,
            },
        },
    )

    with TestClient(main.app) as client:
        response = client.post(
            "/api/telemetry/session-complete",
            json={
                **telemetry_request(consent=True),
                "system_prompt": "Darf nicht genutzt werden",
                "transcript": "Transkriptinhalt",
                "audio": "Audiodaten",
                "speaker_names": {"SPEAKER_00": "Alice"},
                "protocol": "Protokollinhalt",
            },
        )

    assert response.status_code == 200
    assert collector.sent is True
    assert collector.whisper_config == {"model": "test-whisper", "batch_size": 1}
    assert collector.transcription_metrics == {
        "audio_duration_seconds": 61.2,
        "transcription_duration_seconds": 12.4,
        "transcript_line_count": 9,
        "transcript_char_count": 300,
    }
    assert collector.summarization_metrics == {
        "llm_model": "qwen3:8b",
        "system_prompt_kind": "custom",
        "top_count": 2,
        "summarization_duration_seconds": 3.5,
        "protocol_char_count": 120,
    }


def test_telemetry_event_excludes_prompt_content_and_free_form_error_text():
    event = telemetry.TelemetryEvent(
        llm_model="qwen3:8b",
        system_prompt_kind="custom",
        top_count=2,
        protocol_char_count=120,
        error_type="timeout",
    )

    payload = event.to_dict()

    assert payload["system_prompt_kind"] == "custom"
    assert payload["error_type"] == "timeout"
    assert "system_prompt" not in payload
    assert "error" not in payload


def test_local_telemetry_backups_are_optional_and_retention_limited(tmp_path, monkeypatch):
    backup_dir = tmp_path / "telemetry"
    backup_dir.mkdir()
    old_backup = backup_dir / "telemetry_20000101.jsonl"
    old_backup.write_text("{}\n", encoding="utf-8")
    old_time = time.time() - (30 * 24 * 60 * 60)
    os.utime(old_backup, (old_time, old_time))

    monkeypatch.setattr(telemetry, "TELEMETRY_BACKUP_DIR", backup_dir)
    monkeypatch.setattr(telemetry, "TELEMETRY_BACKUP_ENABLED", True)
    monkeypatch.setattr(telemetry, "TELEMETRY_BACKUP_RETENTION_DAYS", 14)
    monkeypatch.setattr(telemetry, "TELEMETRY_BACKUP_MAX_FILES", 30)

    telemetry._save_backup(telemetry.TelemetryEvent(top_count=1))

    backups = sorted(Path(backup_dir).glob("telemetry_*.jsonl"))
    assert old_backup not in backups
    assert len(backups) == 1
