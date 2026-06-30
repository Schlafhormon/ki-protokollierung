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
        "speaker_profiles",
        "speaker_embeddings",
        "job_speaker_embeddings",
        "speaker_observations",
        "pipeline_jobs",
        "summaries",
        "summary_reviews",
        "session_transcript_lines",
    }.issubset(tables)


def test_persistence_migration_is_idempotent_and_keeps_session_state(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "sessions.sqlite3"
    monkeypatch.setenv("PERSISTENCE_DB_PATH", str(db_path))

    persistence.init_db()
    persistence.save_session(
        "session-idempotent",
        {
            "current_step": 2,
            "speaker_names": {"SPEAKER_00": "Alice"},
            "tops": ["Begrüßung"],
            "assignments": [0],
        },
    )

    persistence.init_db()
    persistence.init_db()

    session = persistence.load_session("session-idempotent")

    assert session["current_step"] == 2
    assert session["speaker_names"] == {"SPEAKER_00": "Alice"}
    assert session["tops"] == ["Begrüßung"]
    assert session["assignments"] == [0]


def test_speaker_profiles_embeddings_observations_and_pipeline_jobs_roundtrip(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "sessions.sqlite3"
    monkeypatch.setenv("PERSISTENCE_DB_PATH", str(db_path))
    persistence.init_db()

    persistence.save_job(
        "job-speakers",
        {
            "created_at": 1.0,
            "updated_at": 1.0,
            "session_id": "session-speakers",
            "status": "completed",
            "progress": 100,
            "message": "Transkription abgeschlossen",
            "transcript": [
                {
                    "speaker": "SPEAKER_00",
                    "text": "Hallo",
                    "start": 0.0,
                    "end": 1.0,
                }
            ],
        },
    )
    persistence.save_session(
        "session-speakers",
        {
            "job_id": "job-speakers",
            "speaker_names": {"SPEAKER_00": "Alice lokal"},
            "tops": ["TOP 1"],
            "assignments": [0],
        },
    )

    profile = persistence.create_speaker_profile(
        "Alice Global",
        scope="committee-1",
        profile_id="profile-alice",
    )
    renamed = persistence.rename_speaker_profile("profile-alice", "Alice Beispiel")
    embedding = persistence.save_speaker_embedding(
        "profile-alice",
        [0.1, 0.2, 0.3],
        model_name="test-speaker-model",
        quality=0.87,
        metadata={"source_job_id": "job-speakers"},
    )
    job_embedding = persistence.save_job_speaker_embedding(
        job_id="job-speakers",
        local_speaker_id="SPEAKER_00",
        embedding=[0.2, 0.1, 0.4],
        model_name="test-speaker-model",
        quality=0.7,
        quality_metadata={"total_seconds": 9.0},
    )
    blob_embedding = persistence.save_speaker_embedding(
        "profile-alice",
        b"binary-embedding",
        model_name="test-speaker-model-bin",
    )

    observation = persistence.save_speaker_observation(
        job_id="job-speakers",
        session_id="session-speakers",
        local_speaker_id="SPEAKER_00",
        profile_id="profile-alice",
        confidence=0.76,
        status="suggested",
    )
    confirmed = persistence.confirm_speaker_observation(
        observation["observation_id"],
        confidence=0.91,
    )
    rejected = persistence.reject_speaker_observation(observation["observation_id"])

    pipeline_job = persistence.save_pipeline_job(
        "pipeline-1",
        {
            "session_id": "session-speakers",
            "transcription_job_id": "job-speakers",
            "status": "processing",
            "stage": "assignment",
            "progress": 40,
            "result_refs": {
                "transcription_job_id": "job-speakers",
                "session_id": "session-speakers",
            },
        },
    )
    loaded_pipeline_job = persistence.load_pipeline_job("pipeline-1")

    assert profile["display_name"] == "Alice Global"
    assert renamed["display_name"] == "Alice Beispiel"
    assert persistence.load_speaker_profile("profile-alice")["scope"] == "committee-1"
    assert persistence.load_speaker_profiles(scope="committee-1")[0]["profile_id"] == (
        "profile-alice"
    )
    assert embedding["embedding"] == [0.1, 0.2, 0.3]
    assert embedding["model_name"] == "test-speaker-model"
    assert embedding["metadata"] == {"source_job_id": "job-speakers"}
    assert job_embedding["embedding"] == [0.2, 0.1, 0.4]
    assert persistence.load_job_speaker_embeddings("job-speakers")[0][
        "quality_metadata"
    ] == {"total_seconds": 9.0}
    assert blob_embedding["embedding"] == b"binary-embedding"
    assert persistence.load_speaker_embeddings(
        "profile-alice",
        model_name="test-speaker-model",
    )[0]["quality"] == 0.87
    assert confirmed["status"] == "confirmed"
    assert confirmed["confidence"] == 0.91
    assert rejected["status"] == "rejected"
    assert persistence.load_speaker_observations(
        job_id="job-speakers",
        session_id="session-speakers",
    )[0]["local_speaker_id"] == "SPEAKER_00"
    assert pipeline_job["progress"] == 40
    assert loaded_pipeline_job["result_refs"]["session_id"] == "session-speakers"

    archived = persistence.archive_speaker_profile("profile-alice")

    assert archived["archived_at"] is not None
    assert persistence.load_speaker_profile("profile-alice") is None
    assert persistence.load_speaker_profile(
        "profile-alice",
        include_archived=True,
    )["display_name"] == "Alice Beispiel"
    assert persistence.load_session("session-speakers")["speaker_names"] == {
        "SPEAKER_00": "Alice lokal"
    }


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
            "transcript": [
                {
                    "speaker": "SPEAKER_00",
                    "text": "Korrigierter Bericht zu TOP 1",
                    "start": 0.0,
                    "end": 2.0,
                },
                {
                    "speaker": "SPEAKER_01",
                    "text": "Korrigierter Beschluss zu TOP 2",
                    "start": 2.0,
                    "end": 4.0,
                },
            ],
            "assignments": [0, 1],
            "speaker_names": {
                "SPEAKER_00": "Alice",
                "SPEAKER_01": "Bob",
            },
            "summaries": {
                "0": "Begrüßung wurde abgeschlossen.",
                "1": "Haushalt wurde beschlossen.",
            },
            "export_metadata": {
                "committee": "Hauptausschuss",
                "date": "2026-06-30",
                "location": "Rathaus",
                "title": "Sitzung Hauptausschuss",
                "participants": ["Alice", "Bob"],
                "includeSpeakerList": True,
                "includeTranscriptExcerpt": False,
                "includeGenerationNote": True,
            },
            "summary_reviews": {
                "1": {
                    "structured": {
                        "discussion": [],
                        "decisions": ["Haushalt wurde beschlossen."],
                        "votes": [],
                        "action_items": [],
                        "open_points": [],
                        "uncertainties": [],
                    },
                    "source_links": [
                        {
                            "section": "decisions",
                            "item_index": 0,
                            "item_text": "Haushalt wurde beschlossen.",
                            "line_indices": [1],
                            "start": 2.0,
                            "end": 4.0,
                            "excerpt": "Beschluss zu TOP 2",
                            "confidence": 0.9,
                            "missing_source": False,
                        }
                    ],
                    "review_warnings": [],
                }
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
    assert body["summary_reviews"]["1"]["source_links"][0]["line_indices"] == [1]
    assert body["export_metadata"]["committee"] == "Hauptausschuss"
    assert body["export_metadata"]["participants"] == ["Alice", "Bob"]
    assert body["transcript"][0]["text"] == "Korrigierter Bericht zu TOP 1"
    assert body["transcript"][1]["text"] == "Korrigierter Beschluss zu TOP 2"
    assert body["audio_url"] == "/api/audio/job-2"
    assert body["job"]["status"] == "completed"

    main.jobs.clear()
