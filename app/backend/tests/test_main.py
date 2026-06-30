import time
import threading
from pathlib import Path

from fastapi.testclient import TestClient

import main
import persistence
from conftest import FakeTranscriptionResult


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
