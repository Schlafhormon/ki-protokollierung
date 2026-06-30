import time

import main


def test_audio_upload_validation_accepts_supported_content_types_and_extensions():
    assert main.is_allowed_audio_file("recording.bin", "audio/mpeg")
    assert main.is_allowed_audio_file("recording.M4A", "application/octet-stream")
    assert main.is_allowed_audio_file("meeting.wav", None)


def test_audio_upload_validation_rejects_unsupported_files():
    assert not main.is_allowed_audio_file("agenda.pdf", "application/pdf")
    assert not main.is_allowed_audio_file("notes.txt", "text/plain")
    assert not main.is_allowed_audio_file(None, None)


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
