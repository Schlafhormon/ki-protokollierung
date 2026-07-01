import json
import time
import threading
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

import main
import persistence
import summarize
from conftest import FakeTranscriptionResult
from speaker_recognition import LocalSpeakerEmbedding


def wait_until(predicate, timeout=5.0, interval=0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def configure_test_app(tmp_path, monkeypatch, *, concurrency=1):
    db_path = tmp_path / "sessions.sqlite3"
    upload_dir = tmp_path / "uploads"
    monkeypatch.setenv("PERSISTENCE_DB_PATH", str(db_path))
    monkeypatch.setattr(main, "UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(main, "TRANSCRIPTION_CONCURRENCY", concurrency)
    persistence.init_db()
    main.jobs.clear()
    return upload_dir


def test_audio_upload_validation_accepts_supported_content_types_and_extensions():
    assert main.is_allowed_audio_file("recording.bin", "audio/mpeg")
    assert main.is_allowed_audio_file("recording.M4A", "application/octet-stream")
    assert main.is_allowed_audio_file("meeting.wav", None)


def test_audio_upload_validation_rejects_unsupported_files():
    assert not main.is_allowed_audio_file("agenda.pdf", "application/pdf")
    assert not main.is_allowed_audio_file("notes.txt", "text/plain")
    assert not main.is_allowed_audio_file(None, None)


def test_audio_filename_is_normalized_and_not_used_directly_in_path(tmp_path, monkeypatch):
    upload_dir = configure_test_app(tmp_path, monkeypatch)

    def fake_transcribe(file_path, models, progress_callback=None):
        return FakeTranscriptionResult(
            transcript=[
                {
                    "speaker": "SPEAKER_00",
                    "text": "Hallo",
                    "start": 0.0,
                    "end": 1.0,
                }
            ],
            audio_duration_seconds=1.0,
        )

    monkeypatch.setattr(main, "transcribe_audio", fake_transcribe)

    with TestClient(main.app) as client:
        response = client.post(
            "/api/transcribe",
            files={"audio": ("..\\unsafe name ä.mp3", b"audio", "audio/mpeg")},
        )

        assert response.status_code == 200
        job_id = response.json()["job_id"]

        assert wait_until(
            lambda: client.get(f"/api/transcribe/{job_id}").json()["status"]
            == "completed"
        )
        job = main.get_job_from_cache_or_db(job_id)

    assert job["audio_filename"] == "unsafe_name_a.mp3"
    assert Path(job["file_path"]).parent == upload_dir
    assert Path(job["file_path"]).name == f"{job_id}.mp3"


def test_audio_upload_rejects_files_larger_than_configured_limit(tmp_path, monkeypatch):
    upload_dir = configure_test_app(tmp_path, monkeypatch)
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 5)

    with TestClient(main.app) as client:
        response = client.post(
            "/api/transcribe",
            files={"audio": ("meeting.mp3", b"123456", "audio/mpeg")},
        )

    assert response.status_code == 413
    assert not list(upload_dir.glob("*"))


def test_transcription_jobs_are_queued_and_respect_concurrency_limit(tmp_path, monkeypatch):
    configure_test_app(tmp_path, monkeypatch, concurrency=1)
    first_started = threading.Event()
    release_first = threading.Event()
    calls = []

    def fake_transcribe(file_path, models, progress_callback=None):
        calls.append(file_path)
        if len(calls) == 1:
            first_started.set()
            assert release_first.wait(timeout=5)
        if progress_callback:
            progress_callback(80, "Fast fertig")
        return FakeTranscriptionResult(
            transcript=[
                {
                    "speaker": "SPEAKER_00",
                    "text": Path(file_path).name,
                    "start": 0.0,
                    "end": 1.0,
                }
            ],
            audio_duration_seconds=1.0,
        )

    monkeypatch.setattr(main, "transcribe_audio", fake_transcribe)

    with TestClient(main.app) as client:
        first = client.post(
            "/api/transcribe",
            files={"audio": ("first.mp3", b"one", "audio/mpeg")},
        ).json()["job_id"]
        second = client.post(
            "/api/transcribe",
            files={"audio": ("second.mp3", b"two", "audio/mpeg")},
        ).json()["job_id"]

        assert first_started.wait(timeout=5)
        assert wait_until(
            lambda: client.get(f"/api/transcribe/{first}").json()["status"]
            == "processing"
        )
        assert client.get(f"/api/transcribe/{second}").json()["status"] == "pending"
        assert len(calls) == 1

        release_first.set()

        assert wait_until(
            lambda: client.get(f"/api/transcribe/{first}").json()["status"]
            == "completed"
        )
        assert wait_until(
            lambda: client.get(f"/api/transcribe/{second}").json()["status"]
            == "completed"
        )


def test_transcription_creates_reviewable_speaker_suggestions(
    tmp_path, monkeypatch
):
    configure_test_app(tmp_path, monkeypatch, concurrency=1)
    monkeypatch.setenv("SPEAKER_EMBEDDING_MODEL", "test-embedding")
    monkeypatch.setenv("SPEAKER_MATCH_AUTO_THRESHOLD", "0.98")
    monkeypatch.setenv("SPEAKER_MATCH_SUGGEST_THRESHOLD", "0.9")
    profile = persistence.create_speaker_profile("Alice Global", profile_id="alice")
    persistence.save_speaker_embedding(
        profile["profile_id"],
        [1.0, 0.0],
        model_name="test-embedding",
        quality=1.0,
    )

    def fake_transcribe(file_path, models, progress_callback=None):
        return SimpleNamespace(
            transcript=[
                {
                    "speaker": "SPEAKER_00",
                    "text": "Hallo",
                    "start": 0.0,
                    "end": 1.0,
                }
            ],
            audio_duration_seconds=1.0,
            speaker_embeddings=[
                LocalSpeakerEmbedding(
                    local_speaker_id="SPEAKER_00",
                    embedding=[0.95, 0.05],
                    model_name="test-embedding",
                    quality=0.9,
                    quality_metadata={"total_seconds": 9.0},
                )
            ],
        )

    monkeypatch.setattr(main, "transcribe_audio", fake_transcribe)

    with TestClient(main.app) as client:
        response = client.post(
            "/api/transcribe",
            data={"session_id": "session-speaker-match"},
            files={"audio": ("meeting.mp3", b"audio", "audio/mpeg")},
        )
        assert response.status_code == 200
        job_id = response.json()["job_id"]

        assert wait_until(
            lambda: client.get(f"/api/transcribe/{job_id}").json()["status"]
            == "completed"
        )
        body = client.get(f"/api/transcribe/{job_id}").json()

    assert body["speaker_suggestions"] == [
        {
            "observation_id": 1,
            "local_speaker_id": "SPEAKER_00",
            "profile_id": "alice",
            "profile_display_name": "Alice Global",
            "confidence": body["speaker_suggestions"][0]["confidence"],
            "status": "suggested",
        }
    ]
    assert body["speaker_suggestions"][0]["confidence"] >= 0.98
    observations = persistence.load_speaker_observations(
        job_id=job_id,
        session_id="session-speaker-match",
    )
    assert observations[0]["status"] == "suggested"


def test_cancel_pending_job_marks_cancelled_and_cleans_upload(tmp_path, monkeypatch):
    configure_test_app(tmp_path, monkeypatch, concurrency=1)
    first_started = threading.Event()
    release_first = threading.Event()

    def fake_transcribe(file_path, models, progress_callback=None):
        first_started.set()
        assert release_first.wait(timeout=5)
        return FakeTranscriptionResult(transcript=[], audio_duration_seconds=0)

    monkeypatch.setattr(main, "transcribe_audio", fake_transcribe)

    with TestClient(main.app) as client:
        first = client.post(
            "/api/transcribe",
            files={"audio": ("first.mp3", b"one", "audio/mpeg")},
        ).json()["job_id"]
        second = client.post(
            "/api/transcribe",
            files={"audio": ("second.mp3", b"two", "audio/mpeg")},
        ).json()["job_id"]

        assert first_started.wait(timeout=5)
        pending_job = main.get_job_from_cache_or_db(second)
        pending_upload = Path(pending_job["file_path"])
        assert pending_upload.exists()

        cancel_response = client.post(f"/api/transcribe/{second}/cancel")

        assert cancel_response.status_code == 200
        assert cancel_response.json()["status"] == "cancelled"
        assert not pending_upload.exists()
        assert main.get_job_from_cache_or_db(second)["file_path"] is None

        release_first.set()
        assert wait_until(
            lambda: client.get(f"/api/transcribe/{first}").json()["status"]
            == "completed"
        )


def test_cancel_processing_job_is_terminal_and_cleans_upload_after_worker_stops(
    tmp_path, monkeypatch
):
    configure_test_app(tmp_path, monkeypatch, concurrency=1)
    started = threading.Event()

    def fake_transcribe(file_path, models, progress_callback=None):
        started.set()
        assert wait_until(
            lambda: any(
                job.get("cancellation_requested") for job in main.jobs.values()
            ),
            timeout=5,
        )
        if progress_callback:
            progress_callback(50, "Wird abgebrochen")
        return FakeTranscriptionResult(transcript=[], audio_duration_seconds=0)

    monkeypatch.setattr(main, "transcribe_audio", fake_transcribe)

    with TestClient(main.app) as client:
        job_id = client.post(
            "/api/transcribe",
            files={"audio": ("meeting.mp3", b"audio", "audio/mpeg")},
        ).json()["job_id"]

        assert started.wait(timeout=5)
        job = main.get_job_from_cache_or_db(job_id)
        upload_path = Path(job["file_path"])
        assert upload_path.exists()

        cancel_response = client.post(f"/api/transcribe/{job_id}/cancel")

        assert cancel_response.status_code == 200
        assert cancel_response.json()["status"] == "cancelled"
        assert wait_until(lambda: not upload_path.exists())
        final_job = client.get(f"/api/transcribe/{job_id}").json()
        assert final_job["status"] == "cancelled"
        assert final_job["audio_url"] is None


def test_pdf_upload_validation_accepts_pdf_mime_or_extension():
    assert main.is_allowed_pdf_file("agenda.bin", "application/pdf")
    assert main.is_allowed_pdf_file("agenda.PDF", "application/octet-stream")


def test_assignment_suggestions_endpoint_returns_reviewable_segments():
    with TestClient(main.app) as client:
        response = client.post(
            "/api/assignment-suggestions",
            json={
                "tops": ["1. Begrüßung", "2. Haushalt"],
                "transcript": [
                    {
                        "speaker": "SPEAKER_00",
                        "text": "Ich eröffne die Sitzung.",
                        "start": 0,
                        "end": 3,
                    },
                    {
                        "speaker": "SPEAKER_00",
                        "text": "Kommen wir zu TOP 2 Haushalt.",
                        "start": 4,
                        "end": 8,
                    },
                ],
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["suggested_assignments"] == [0, 1]
    assert data["segments"][1]["confidence"] >= 0.7
    assert data["segments"][1]["reason"]


def test_agenda_detection_endpoint_detects_tops_without_pdf_or_manual_list():
    with TestClient(main.app) as client:
        response = client.post(
            "/api/agenda-detection",
            json={
                "transcript": [
                    {
                        "speaker": "SPEAKER_00",
                        "text": "Kommen wir zu TOP 1 Haushalt.",
                        "start": 0,
                        "end": 3,
                    },
                    {
                        "speaker": "SPEAKER_01",
                        "text": "Der Haushalt wird beraten.",
                        "start": 4,
                        "end": 8,
                    },
                    {
                        "speaker": "SPEAKER_00",
                        "text": "Als nächstes rufe ich TOP 2 Schulbau auf.",
                        "start": 9,
                        "end": 12,
                    },
                ],
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["tops"] == ["Haushalt", "Schulbau"]
    assert data["assignments"] == [0, 0, 1]
    assert data["segments"][0]["evidence_text"] == "Kommen wir zu TOP 1 Haushalt."
    assert data["strategy"] == "heuristic_transcript_fallback"


def test_pipeline_runs_to_reviewable_result_and_persists_status_after_cache_clear(
    tmp_path, monkeypatch
):
    configure_test_app(tmp_path, monkeypatch, concurrency=1)

    def fake_transcribe(file_path, models, progress_callback=None):
        if progress_callback:
            progress_callback(40, "Transkription läuft")
        return FakeTranscriptionResult(
            transcript=[
                {
                    "speaker": "SPEAKER_00",
                    "text": "Ich eröffne TOP 1 Haushalt.",
                    "start": 0.0,
                    "end": 2.0,
                },
                {
                    "speaker": "SPEAKER_01",
                    "text": "Der Haushalt wird beraten.",
                    "start": 2.0,
                    "end": 5.0,
                },
                {
                    "speaker": "SPEAKER_00",
                    "text": "Kommen wir zu TOP 2 Schulbau.",
                    "start": 5.0,
                    "end": 7.0,
                },
            ],
            audio_duration_seconds=7.0,
        )

    def fake_summarize_segment(top_title, transcript_text, model=None, system_prompt=None):
        structured = summarize.StructuredSummary(
            discussion=[f"{top_title} wurde beraten."]
        )
        return summarize.SummarizationResult(
            summary=f"Zusammenfassung {top_title}",
            duration_seconds=0.01,
            structured=structured,
        )

    monkeypatch.setattr(main, "transcribe_audio", fake_transcribe)
    monkeypatch.setattr(main, "summarize_segment", fake_summarize_segment)

    with TestClient(main.app) as client:
        response = client.post(
            "/api/pipeline/start",
            data={
                "session_id": "pipeline-session",
                "tops": json.dumps(["TOP 1 Haushalt", "TOP 2 Schulbau"]),
                "options": json.dumps({"model": "fake-llm"}),
            },
            files={"audio": ("meeting.mp3", b"audio", "audio/mpeg")},
        )

        assert response.status_code == 200
        pipeline_id = response.json()["pipeline_id"]

        assert wait_until(
            lambda: client.get(f"/api/pipeline/{pipeline_id}").json()["stage"]
            == "ready_for_review"
        )
        status = client.get(f"/api/pipeline/{pipeline_id}").json()
        assert status["status"] == "completed"
        assert status["progress"] == 100

        main.jobs.clear()
        restored_status = client.get(f"/api/pipeline/{pipeline_id}").json()
        assert restored_status["stage"] == "ready_for_review"

        result = client.get(f"/api/pipeline/{pipeline_id}/result").json()
        session = result["session"]
        assert session["session_id"] == "pipeline-session"
        assert session["job"]["status"] == "completed"
        assert session["tops"] == ["TOP 1 Haushalt", "TOP 2 Schulbau"]
        assert session["assignments"] == [0, 0, 1]
        assert session["summaries"]["0"] == "Zusammenfassung TOP 1 Haushalt"
        assert session["summary_reviews"]["0"]["structured"]["discussion"]
        assert session["audio_url"].startswith("/api/audio/")

        session["summaries"]["0"] = "Manuell korrigierte Zusammenfassung."
        save_response = client.put(
            "/api/sessions/pipeline-session",
            json={
                "job_id": session["job_id"],
                "current_step": session["current_step"],
                "tops": session["tops"],
                "transcript": session["transcript"],
                "assignments": session["assignments"],
                "speaker_names": session["speaker_names"],
                "summaries": session["summaries"],
                "summary_reviews": session["summary_reviews"],
                "export_metadata": {
                    "committee": "Hauptausschuss",
                    "date": "2026-07-01",
                    "location": "Rathaus",
                    "title": "Pipeline-Protokoll",
                    "participants": [],
                },
                "skipped_assignment": False,
            },
        )
        assert save_response.status_code == 200

        export_response = client.post(
            "/api/export",
            json={
                "format": "txt",
                "metadata": {
                    "committee": "Hauptausschuss",
                    "date": "2026-07-01",
                    "location": "Rathaus",
                    "title": "Pipeline-Protokoll",
                    "participants": [],
                },
                "appendix": {
                    "include_speaker_list": True,
                    "include_transcript_excerpt": False,
                    "include_generation_note": True,
                },
                "tops": session["tops"],
                "transcript": session["transcript"],
                "assignments": session["assignments"],
                "speaker_names": session["speaker_names"],
                "summaries": session["summaries"],
                "summary_reviews": session["summary_reviews"],
            },
        )
        assert export_response.status_code == 200
        assert "Manuell korrigierte Zusammenfassung." in export_response.text


def test_pipeline_falls_back_to_full_conversation_when_agenda_detection_fails(
    tmp_path, monkeypatch
):
    configure_test_app(tmp_path, monkeypatch, concurrency=1)

    monkeypatch.setattr(
        main,
        "transcribe_audio",
        lambda file_path, models, progress_callback=None: FakeTranscriptionResult(
            transcript=[
                {
                    "speaker": "SPEAKER_00",
                    "text": "Wir beraten ohne erkennbare Tagesordnung.",
                    "start": 0.0,
                    "end": 3.0,
                }
            ],
            audio_duration_seconds=3.0,
        ),
    )
    monkeypatch.setattr(
        main,
        "detect_agenda_from_transcript",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("agenda kaputt")),
    )
    monkeypatch.setattr(
        main,
        "summarize_segment",
        lambda *args, **kwargs: summarize.SummarizationResult(
            summary="Gesamtes Gespräch wurde zusammengefasst.",
            duration_seconds=0.01,
            structured=None,
            fallback_used=True,
        ),
    )

    with TestClient(main.app) as client:
        response = client.post(
            "/api/pipeline/start",
            files={"audio": ("meeting.mp3", b"audio", "audio/mpeg")},
        )
        pipeline_id = response.json()["pipeline_id"]

        assert wait_until(
            lambda: client.get(f"/api/pipeline/{pipeline_id}").json()["stage"]
            == "ready_for_review"
        )
        result = client.get(f"/api/pipeline/{pipeline_id}/result").json()

    assert result["session"]["tops"] == ["Gesamtes Gespräch"]
    assert result["session"]["assignments"] == [0]
    assert result["warnings"]


def test_pipeline_marks_failed_top_summary_but_stays_reviewable(
    tmp_path, monkeypatch
):
    configure_test_app(tmp_path, monkeypatch, concurrency=1)

    monkeypatch.setattr(
        main,
        "transcribe_audio",
        lambda file_path, models, progress_callback=None: FakeTranscriptionResult(
            transcript=[
                {
                    "speaker": "SPEAKER_00",
                    "text": "TOP 1 Haushalt wird beraten.",
                    "start": 0.0,
                    "end": 2.0,
                },
                {
                    "speaker": "SPEAKER_00",
                    "text": "TOP 2 Fehler wird beraten.",
                    "start": 2.0,
                    "end": 4.0,
                },
            ],
            audio_duration_seconds=4.0,
        ),
    )

    def maybe_fail_summary(top_title, transcript_text, model=None, system_prompt=None):
        if "Fehler" in top_title:
            raise RuntimeError("LLM nicht verfügbar")
        return summarize.SummarizationResult(
            summary="Haushalt wurde zusammengefasst.",
            duration_seconds=0.01,
            structured=None,
        )

    monkeypatch.setattr(main, "summarize_segment", maybe_fail_summary)

    with TestClient(main.app) as client:
        response = client.post(
            "/api/pipeline/start",
            data={"tops": json.dumps(["TOP 1 Haushalt", "TOP 2 Fehler"])},
            files={"audio": ("meeting.mp3", b"audio", "audio/mpeg")},
        )
        pipeline_id = response.json()["pipeline_id"]

        assert wait_until(
            lambda: client.get(f"/api/pipeline/{pipeline_id}").json()["stage"]
            == "ready_for_review"
        )
        result = client.get(f"/api/pipeline/{pipeline_id}/result").json()

    assert result["pipeline"]["status"] == "completed"
    assert result["session"]["summaries"]["0"] == "Haushalt wurde zusammengefasst."
    assert result["session"]["summaries"]["1"] == ""
    assert result["session"]["summary_reviews"]["1"]["review_warnings"][0]["severity"] == "error"
    assert result["warnings"]


def test_pipeline_cancel_endpoint_marks_pending_pipeline_cancelled(tmp_path, monkeypatch):
    configure_test_app(tmp_path, monkeypatch, concurrency=1)
    audio_path = tmp_path / "pending.mp3"
    audio_path.write_bytes(b"audio")

    with TestClient(main.app) as client:
        persistence.save_job(
            "cancel-transcription",
            {
                "session_id": "cancel-session",
                "status": "pending",
                "progress": 0,
                "message": "wartet",
                "file_path": str(audio_path),
                "audio_path": str(audio_path),
            },
        )
        persistence.save_pipeline_job(
            "cancel-pipeline",
            {
                "session_id": "cancel-session",
                "transcription_job_id": "cancel-transcription",
                "status": "pending",
                "stage": "upload",
                "progress": 5,
                "result_refs": {"audio_path": str(audio_path), "warnings": []},
            },
        )
        response = client.post("/api/pipeline/cancel-pipeline/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert persistence.load_pipeline_job("cancel-pipeline")["result_refs"][
        "cancel_requested"
    ]


def test_cleanup_old_jobs_removes_expired_jobs_and_upload_files(tmp_path, monkeypatch):
    old_file_path = tmp_path / "old-upload.mp3"
    old_audio_path = tmp_path / "old-audio.mp3"
    fresh_file_path = tmp_path / "fresh-upload.mp3"
    old_file_path.write_bytes(b"old upload")
    old_audio_path.write_bytes(b"old audio")
    fresh_file_path.write_bytes(b"fresh upload")

    main.jobs.clear()
    monkeypatch.setattr(main, "JOB_MAX_AGE_SECONDS", 60)
    monkeypatch.setattr(main, "JOB_MAX_COUNT", 10)
    monkeypatch.setattr(main, "DELETE_UPLOADS_ON_JOB_CLEANUP", True)

    now = time.time()
    main.jobs["old"] = {
        "created_at": now - 120,
        "file_path": str(old_file_path),
        "audio_path": str(old_audio_path),
    }
    main.jobs["fresh"] = {
        "created_at": now,
        "file_path": str(fresh_file_path),
    }

    assert main.cleanup_old_jobs() == 1

    assert list(main.jobs) == ["fresh"]
    assert not old_file_path.exists()
    assert not old_audio_path.exists()
    assert fresh_file_path.exists()

    main.jobs.clear()


def test_cleanup_old_jobs_enforces_max_count(tmp_path, monkeypatch):
    main.jobs.clear()
    monkeypatch.setattr(main, "JOB_MAX_AGE_SECONDS", 3600)
    monkeypatch.setattr(main, "JOB_MAX_COUNT", 2)
    monkeypatch.setattr(main, "DELETE_UPLOADS_ON_JOB_CLEANUP", True)

    files = []
    for index in range(3):
        file_path = tmp_path / f"job-{index}.mp3"
        file_path.write_bytes(b"audio")
        files.append(file_path)
        main.jobs[f"job-{index}"] = {
            "created_at": time.time(),
            "file_path": str(file_path),
        }

    assert main.cleanup_old_jobs() == 1

    assert list(main.jobs) == ["job-1", "job-2"]
    assert not files[0].exists()
    assert files[1].exists()
    assert files[2].exists()

    main.jobs.clear()
