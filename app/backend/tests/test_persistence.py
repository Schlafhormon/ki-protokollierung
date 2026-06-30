import sqlite3

from fastapi.testclient import TestClient

import main
import persistence


def test_persistence_initializes_expected_tables(tmp_path, monkeypatch):
    db_path = tmp_path / "sessions.sqlite3"
    monkeypatch.setenv("PERSISTENCE_DB_PATH", str(db_path))

    persistence.init_db()

    with sqlite3.connect(db_path) as db:
        tables = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

    assert {
        "sessions",
        "transcription_jobs",
        "transcript_lines",
        "tops",
        "assignments",
        "speaker_names",
        "summaries",
    }.issubset(tables)


def test_transcription_job_is_restored_from_sqlite_after_memory_cache_is_cleared(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "sessions.sqlite3"
    audio_path = tmp_path / "meeting.mp3"
    audio_path.write_bytes(b"abcdef")
    monkeypatch.setenv("PERSISTENCE_DB_PATH", str(db_path))
    persistence.init_db()

    persistence.save_job(
        "job-1",
        {
            "created_at": 1.0,
            "updated_at": 1.0,
            "status": "completed",
            "progress": 100,
            "message": "Transkription abgeschlossen",
            "file_path": str(audio_path),
            "audio_path": str(audio_path),
            "audio_filename": "meeting.mp3",
            "audio_content_type": "audio/mpeg",
            "audio_size_bytes": audio_path.stat().st_size,
            "transcript": [
                {
                    "speaker": "SPEAKER_00",
                    "text": "Hallo zusammen",
                    "start": 0.0,
                    "end": 1.2,
                }
            ],
            "telemetry": {"audio_duration_seconds": 1.2},
        },
    )

    main.jobs.clear()
    client = TestClient(main.app)

    response = client.get("/api/transcribe/job-1")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["audio_url"] == "/api/audio/job-1"
    assert body["audio_metadata"] == {
        "filename": "meeting.mp3",
        "content_type": "audio/mpeg",
        "size_bytes": 6,
    }
    assert body["transcript"] == [
        {
            "speaker": "SPEAKER_00",
            "text": "Hallo zusammen",
            "start": 0.0,
            "end": 1.2,
        }
    ]

    audio_response = client.get("/api/audio/job-1", headers={"Range": "bytes=1-3"})
    assert audio_response.status_code == 206
    assert audio_response.content == b"bcd"

    main.jobs.clear()


def test_session_state_is_saved_and_loaded_with_linked_job(tmp_path, monkeypatch):
    db_path = tmp_path / "sessions.sqlite3"
    audio_path = tmp_path / "meeting.mp3"
    audio_path.write_bytes(b"audio")
    monkeypatch.setenv("PERSISTENCE_DB_PATH", str(db_path))
    persistence.init_db()

    persistence.save_job(
        "job-2",
        {
            "created_at": 1.0,
            "updated_at": 1.0,
            "status": "completed",
            "progress": 100,
            "message": "Transkription abgeschlossen",
            "file_path": str(audio_path),
            "audio_path": str(audio_path),
            "transcript": [
                {
                    "speaker": "SPEAKER_00",
                    "text": "Bericht zu TOP 1",
                    "start": 0.0,
                    "end": 2.0,
                },
                {
                    "speaker": "SPEAKER_01",
                    "text": "Beschluss zu TOP 2",
                    "start": 2.0,
                    "end": 4.0,
                },
            ],
        },
    )

    main.jobs.clear()
    client = TestClient(main.app)

    save_response = client.put(
        "/api/sessions/session-1",
        json={
            "job_id": "job-2",
            "current_step": 3,
            "tops": ["Begrüßung", "Haushalt"],
            "assignments": [0, 1],
            "speaker_names": {
                "SPEAKER_00": "Alice",
                "SPEAKER_01": "Bob",
            },
            "summaries": {
                "0": "Begrüßung wurde abgeschlossen.",
                "1": "Haushalt wurde beschlossen.",
            },
            "skipped_assignment": False,
        },
    )

    assert save_response.status_code == 200
    assert save_response.json()["session_id"] == "session-1"

    main.jobs.clear()
    load_response = client.get("/api/sessions/session-1")

    assert load_response.status_code == 200
    body = load_response.json()
    assert body["job_id"] == "job-2"
    assert body["current_step"] == 3
    assert body["tops"] == ["Begrüßung", "Haushalt"]
    assert body["assignments"] == [0, 1]
    assert body["speaker_names"] == {
        "SPEAKER_00": "Alice",
        "SPEAKER_01": "Bob",
    }
    assert body["summaries"] == {
        "0": "Begrüßung wurde abgeschlossen.",
        "1": "Haushalt wurde beschlossen.",
    }
    assert body["transcript"][1]["text"] == "Beschluss zu TOP 2"
    assert body["audio_url"] == "/api/audio/job-2"
    assert body["job"]["status"] == "completed"

    main.jobs.clear()
