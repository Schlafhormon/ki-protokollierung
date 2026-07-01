from fastapi.testclient import TestClient
import pytest

import main
import persistence


def setup_speaker_session(tmp_path, monkeypatch, *, session_id="session-speakers"):
    db_path = tmp_path / "sessions.sqlite3"
    monkeypatch.setenv("PERSISTENCE_DB_PATH", str(db_path))
    persistence.init_db()
    main.jobs.clear()

    persistence.save_job(
        "job-speakers",
        {
            "created_at": 1.0,
            "updated_at": 1.0,
            "session_id": session_id,
            "status": "completed",
            "progress": 100,
            "message": "Transkription abgeschlossen",
            "transcript": [
                {
                    "speaker": "SPEAKER_00",
                    "text": "Hallo",
                    "start": 0.0,
                    "end": 1.0,
                },
                {
                    "speaker": "SPEAKER_01",
                    "text": "Guten Tag",
                    "start": 1.0,
                    "end": 2.0,
                },
            ],
        },
    )
    persistence.save_session(
        session_id,
        {
            "job_id": "job-speakers",
            "speaker_names": {
                "SPEAKER_00": "Lokale Person 0",
                "SPEAKER_01": "Lokale Person 1",
            },
            "transcript": [
                {
                    "speaker": "SPEAKER_00",
                    "text": "Hallo",
                    "start": 0.0,
                    "end": 1.0,
                },
                {
                    "speaker": "SPEAKER_01",
                    "text": "Guten Tag",
                    "start": 1.0,
                    "end": 2.0,
                },
            ],
        },
    )
    return session_id


def test_speaker_profile_crud_archives_instead_of_deleting(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSISTENCE_DB_PATH", str(tmp_path / "sessions.sqlite3"))
    persistence.init_db()
    client = TestClient(main.app)

    created = client.post(
        "/api/speaker-profiles",
        json={"display_name": "Alice Beispiel", "scope": "committee-1"},
    )

    assert created.status_code == 200
    profile_id = created.json()["profile_id"]
    assert created.json()["archived"] is False

    listed = client.get("/api/speaker-profiles?scope=committee-1")
    assert [profile["profile_id"] for profile in listed.json()] == [profile_id]

    updated = client.put(
        f"/api/speaker-profiles/{profile_id}",
        json={"display_name": "Alice Umbenannt"},
    )
    assert updated.status_code == 200
    assert updated.json()["display_name"] == "Alice Umbenannt"
    assert updated.json()["scope"] == "committee-1"

    archived = client.delete(f"/api/speaker-profiles/{profile_id}")
    assert archived.status_code == 200
    assert archived.json()["archived"] is True
    assert archived.json()["archived_at"] is not None

    assert client.get("/api/speaker-profiles").json() == []
    with_archived = client.get("/api/speaker-profiles?include_archived=true").json()
    assert with_archived[0]["profile_id"] == profile_id
    assert client.put(
        f"/api/speaker-profiles/{profile_id}",
        json={"display_name": "Nicht erlaubt"},
    ).status_code == 409


def test_archiving_profile_anonymizes_observations_and_embeddings_can_be_deleted(
    tmp_path, monkeypatch
):
    session_id = setup_speaker_session(tmp_path, monkeypatch)
    profile = persistence.create_speaker_profile("Alice Global", profile_id="alice")
    persistence.save_speaker_embedding(
        profile["profile_id"],
        [1.0, 0.0],
        model_name="test-embedding",
        quality=0.9,
    )
    persistence.save_speaker_observation(
        job_id="job-speakers",
        session_id=session_id,
        local_speaker_id="SPEAKER_00",
        profile_id=profile["profile_id"],
        confidence=0.8,
        status="suggested",
    )
    client = TestClient(main.app)

    deleted = client.delete("/api/speaker-profiles/alice/embeddings")
    assert deleted.status_code == 200
    assert deleted.json()["deleted_count"] == 1
    assert persistence.load_speaker_embeddings("alice") == []

    archived = client.delete("/api/speaker-profiles/alice")
    assert archived.status_code == 200
    assert archived.json()["archived"] is True
    observations = client.get(
        f"/api/sessions/{session_id}/speaker-observations"
    ).json()
    assert observations[0]["profile_id"] is None
    assert observations[0]["profile_display_name"] is None
    assert "Alice Global" not in str(observations)


def test_speaker_observation_confirm_and_reject_flow(tmp_path, monkeypatch):
    session_id = setup_speaker_session(tmp_path, monkeypatch)
    alice = persistence.create_speaker_profile("Alice Global", profile_id="alice")
    bob = persistence.create_speaker_profile("Bob Global", profile_id="bob")
    persistence.save_job_speaker_embedding(
        job_id="job-speakers",
        local_speaker_id="SPEAKER_00",
        embedding=[1.0, 0.0],
        model_name="test-embedding",
        quality=0.9,
        quality_metadata={"total_seconds": 10.0},
    )
    suggested = persistence.save_speaker_observation(
        job_id="job-speakers",
        session_id=session_id,
        local_speaker_id="SPEAKER_00",
        profile_id=alice["profile_id"],
        confidence=0.72,
        status="suggested",
    )
    duplicate = persistence.save_speaker_observation(
        job_id="job-speakers",
        session_id=session_id,
        local_speaker_id="SPEAKER_00",
        profile_id=bob["profile_id"],
        confidence=0.63,
        status="suggested",
    )
    rejectable = persistence.save_speaker_observation(
        job_id="job-speakers",
        session_id=session_id,
        local_speaker_id="SPEAKER_01",
        profile_id=bob["profile_id"],
        confidence=0.68,
        status="suggested",
    )
    client = TestClient(main.app)

    observations = client.get(
        f"/api/sessions/{session_id}/speaker-observations"
    ).json()
    assert observations[0]["profile_display_name"] == "Alice Global"
    assert observations[0]["local_display_name"] == "Lokale Person 0"
    assert observations[0]["display_name"] == "Alice Global"
    assert observations[0]["confidence"] == 0.72

    confirmed = client.post(
        f"/api/sessions/{session_id}/speaker-observations/"
        f"{suggested['observation_id']}/confirm"
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"
    assert confirmed.json()["display_name"] == "Alice Global"
    assert persistence.load_session(session_id)["speaker_names"]["SPEAKER_00"] == (
        "Alice Global"
    )
    profile_embeddings = persistence.load_speaker_embeddings(
        "alice",
        model_name="test-embedding",
    )
    assert profile_embeddings[0]["embedding"] == [1.0, 0.0]
    assert profile_embeddings[0]["quality"] == 0.9
    assert profile_embeddings[0]["metadata"]["source_job_id"] == "job-speakers"
    assert profile_embeddings[0]["metadata"]["source_local_speaker_id"] == (
        "SPEAKER_00"
    )
    assert profile_embeddings[0]["metadata"]["storage_reason"] == "confirm"

    duplicate_response = client.post(
        f"/api/sessions/{session_id}/speaker-observations/"
        f"{duplicate['observation_id']}/confirm"
    )
    assert duplicate_response.status_code == 409

    rejected = client.post(
        f"/api/sessions/{session_id}/speaker-observations/"
        f"{rejectable['observation_id']}/reject"
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"

    assert client.post(
        f"/api/sessions/{session_id}/speaker-observations/"
        f"{suggested['observation_id']}/reject"
    ).status_code == 409


def test_manual_speaker_observation_can_link_existing_or_new_profile(
    tmp_path, monkeypatch
):
    session_id = setup_speaker_session(tmp_path, monkeypatch)
    alice = persistence.create_speaker_profile("Alice Global", profile_id="alice")
    persistence.save_job_speaker_embedding(
        job_id="job-speakers",
        local_speaker_id="SPEAKER_01",
        embedding=[0.0, 1.0],
        model_name="test-embedding",
        quality=0.8,
    )
    client = TestClient(main.app)

    manual = client.post(
        f"/api/sessions/{session_id}/speaker-observations/manual",
        json={"local_speaker_id": "SPEAKER_00", "profile_id": alice["profile_id"]},
    )

    assert manual.status_code == 200
    assert manual.json()["status"] == "manual"
    assert manual.json()["profile_display_name"] == "Alice Global"
    assert persistence.load_session(session_id)["speaker_names"]["SPEAKER_00"] == (
        "Alice Global"
    )

    duplicate = client.post(
        f"/api/sessions/{session_id}/speaker-observations/manual",
        json={"local_speaker_id": "SPEAKER_00", "display_name": "Andere Person"},
    )
    assert duplicate.status_code == 409

    new_profile = client.post(
        f"/api/sessions/{session_id}/speaker-observations/manual",
        json={
            "local_speaker_id": "SPEAKER_01",
            "display_name": "Charlie Global",
            "scope": "committee-1",
        },
    )
    assert new_profile.status_code == 200
    assert new_profile.json()["status"] == "manual"
    assert new_profile.json()["profile_display_name"] == "Charlie Global"
    assert persistence.load_speaker_profiles(scope="committee-1")[0][
        "display_name"
    ] == "Charlie Global"
    created_profile_id = new_profile.json()["profile_id"]
    assert persistence.load_speaker_embeddings(
        created_profile_id,
        model_name="test-embedding",
    )[0]["embedding"] == [0.0, 1.0]


def test_global_embedding_copy_requires_opt_in_or_explicit_action(
    tmp_path, monkeypatch
):
    setup_speaker_session(tmp_path, monkeypatch)
    persistence.create_speaker_profile("Alice Global", profile_id="alice")
    persistence.save_job_speaker_embedding(
        job_id="job-speakers",
        local_speaker_id="SPEAKER_00",
        embedding=[1.0, 0.0],
        model_name="test-embedding",
        quality=0.9,
    )

    with pytest.raises(main.HTTPException) as exc_info:
        main.add_job_embedding_to_profile(
            job_id="job-speakers",
            local_speaker_id="SPEAKER_00",
            profile_id="alice",
        )

    assert exc_info.value.status_code == 403
    assert persistence.load_speaker_embeddings("alice") == []


def test_speaker_observation_errors_are_reported_cleanly(tmp_path, monkeypatch):
    session_id = setup_speaker_session(tmp_path, monkeypatch)
    archived = persistence.create_speaker_profile("Archiviert", profile_id="archived")
    persistence.archive_speaker_profile(archived["profile_id"])
    client = TestClient(main.app)

    assert client.get(
        "/api/sessions/unknown/speaker-observations"
    ).status_code == 404
    assert client.post(
        f"/api/sessions/{session_id}/speaker-observations/manual",
        json={"local_speaker_id": "SPEAKER_00", "profile_id": "missing"},
    ).status_code == 404
    assert client.post(
        f"/api/sessions/{session_id}/speaker-observations/manual",
        json={"local_speaker_id": "SPEAKER_99", "profile_id": archived["profile_id"]},
    ).status_code == 404
    assert client.post(
        f"/api/sessions/{session_id}/speaker-observations/manual",
        json={"local_speaker_id": "SPEAKER_00", "profile_id": archived["profile_id"]},
    ).status_code == 409
    assert client.post(
        f"/api/sessions/{session_id}/speaker-observations/manual",
        json={
            "local_speaker_id": "SPEAKER_00",
            "profile_id": archived["profile_id"],
            "display_name": "Zu viel",
        },
    ).status_code == 400


def test_reading_speaker_observations_does_not_create_profiles(tmp_path, monkeypatch):
    session_id = setup_speaker_session(tmp_path, monkeypatch)
    client = TestClient(main.app)

    response = client.get(f"/api/sessions/{session_id}/speaker-observations")

    assert response.status_code == 200
    assert response.json() == []
    assert client.get("/api/speaker-profiles").json() == []
