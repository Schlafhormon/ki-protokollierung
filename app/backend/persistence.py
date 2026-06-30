"""SQLite persistence for sessions and transcription jobs."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any


DEFAULT_DB_PATH = Path(os.environ.get("PERSISTENCE_DB_PATH", "data/sessions.sqlite3"))


def get_db_path() -> Path:
    return Path(os.environ.get("PERSISTENCE_DB_PATH", str(DEFAULT_DB_PATH)))


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db(db_path: Path | None = None) -> None:
    """Create or migrate the local SQLite database."""
    with connect(db_path) as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                job_id TEXT,
                current_step INTEGER,
                skipped_assignment INTEGER NOT NULL DEFAULT 0,
                export_metadata_json TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS transcription_jobs (
                job_id TEXT PRIMARY KEY,
                session_id TEXT,
                status TEXT NOT NULL,
                progress INTEGER NOT NULL DEFAULT 0,
                message TEXT NOT NULL DEFAULT '',
                file_path TEXT,
                audio_path TEXT,
                audio_filename TEXT,
                audio_content_type TEXT,
                audio_size_bytes INTEGER,
                cancellation_requested INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                telemetry_json TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                    ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS transcript_lines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                line_index INTEGER NOT NULL,
                speaker TEXT NOT NULL,
                text TEXT NOT NULL,
                start REAL NOT NULL,
                end REAL NOT NULL,
                FOREIGN KEY (job_id) REFERENCES transcription_jobs(job_id)
                    ON DELETE CASCADE,
                UNIQUE (job_id, line_index)
            );

            CREATE TABLE IF NOT EXISTS tops (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                top_index INTEGER NOT NULL,
                title TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                    ON DELETE CASCADE,
                UNIQUE (session_id, top_index)
            );

            CREATE TABLE IF NOT EXISTS assignments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                line_index INTEGER NOT NULL,
                top_index INTEGER,
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                    ON DELETE CASCADE,
                UNIQUE (session_id, line_index)
            );

            CREATE TABLE IF NOT EXISTS speaker_names (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                speaker_id TEXT NOT NULL,
                display_name TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                    ON DELETE CASCADE,
                UNIQUE (session_id, speaker_id)
            );

            CREATE TABLE IF NOT EXISTS summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                top_index INTEGER NOT NULL,
                summary TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                    ON DELETE CASCADE,
                UNIQUE (session_id, top_index)
            );

            CREATE TABLE IF NOT EXISTS summary_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                top_index INTEGER NOT NULL,
                review_json TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                    ON DELETE CASCADE,
                UNIQUE (session_id, top_index)
            );

            CREATE TABLE IF NOT EXISTS session_transcript_lines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                line_index INTEGER NOT NULL,
                speaker TEXT NOT NULL,
                text TEXT NOT NULL,
                start REAL NOT NULL,
                end REAL NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                    ON DELETE CASCADE,
                UNIQUE (session_id, line_index)
            );

            CREATE INDEX IF NOT EXISTS idx_transcription_jobs_session_id
                ON transcription_jobs(session_id);
            CREATE INDEX IF NOT EXISTS idx_transcript_lines_job_id
                ON transcript_lines(job_id);
            CREATE INDEX IF NOT EXISTS idx_session_transcript_lines_session_id
                ON session_transcript_lines(session_id);
            """
        )
        existing_columns = {
            row["name"]
            for row in db.execute("PRAGMA table_info(transcription_jobs)").fetchall()
        }
        if "cancellation_requested" not in existing_columns:
            db.execute(
                """
                ALTER TABLE transcription_jobs
                ADD COLUMN cancellation_requested INTEGER NOT NULL DEFAULT 0
                """
            )
        session_columns = {
            row["name"] for row in db.execute("PRAGMA table_info(sessions)").fetchall()
        }
        if "export_metadata_json" not in session_columns:
            db.execute(
                """
                ALTER TABLE sessions
                ADD COLUMN export_metadata_json TEXT
                """
            )


def _to_json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def _from_json(value: str | None) -> Any:
    if not value:
        return None
    return json.loads(value)


def save_job(job_id: str, job_data: dict[str, Any], db_path: Path | None = None) -> None:
    """Upsert a transcription job and its transcript lines."""
    now = time.time()
    created_at = float(job_data.get("created_at") or now)
    updated_at = float(job_data.get("updated_at") or now)
    transcript = job_data.get("transcript")

    with connect(db_path) as db:
        if job_data.get("session_id"):
            db.execute(
                """
                INSERT INTO sessions (
                    session_id, job_id, current_step, skipped_assignment,
                    created_at, updated_at
                )
                VALUES (?, ?, NULL, 0, ?, ?)
                ON CONFLICT(session_id) DO NOTHING
                """,
                (job_data["session_id"], job_id, created_at, updated_at),
            )

        db.execute(
            """
            INSERT INTO transcription_jobs (
                job_id, session_id, status, progress, message, file_path, audio_path,
                audio_filename, audio_content_type, audio_size_bytes,
                cancellation_requested, error, telemetry_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                session_id = excluded.session_id,
                status = excluded.status,
                progress = excluded.progress,
                message = excluded.message,
                file_path = excluded.file_path,
                audio_path = excluded.audio_path,
                audio_filename = excluded.audio_filename,
                audio_content_type = excluded.audio_content_type,
                audio_size_bytes = excluded.audio_size_bytes,
                cancellation_requested = excluded.cancellation_requested,
                error = excluded.error,
                telemetry_json = excluded.telemetry_json,
                updated_at = excluded.updated_at
            """,
            (
                job_id,
                job_data.get("session_id"),
                job_data.get("status", "pending"),
                int(job_data.get("progress", 0)),
                job_data.get("message", ""),
                job_data.get("file_path"),
                job_data.get("audio_path"),
                job_data.get("audio_filename"),
                job_data.get("audio_content_type"),
                job_data.get("audio_size_bytes"),
                1 if job_data.get("cancellation_requested") else 0,
                job_data.get("error"),
                _to_json(job_data.get("telemetry")),
                created_at,
                updated_at,
            ),
        )

        if job_data.get("session_id"):
            db.execute(
                """
                UPDATE sessions
                SET job_id = ?, updated_at = ?
                WHERE session_id = ?
                """,
                (job_id, updated_at, job_data["session_id"]),
            )

        if transcript is not None:
            db.execute("DELETE FROM transcript_lines WHERE job_id = ?", (job_id,))
            db.executemany(
                """
                INSERT INTO transcript_lines (
                    job_id, line_index, speaker, text, start, end
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        job_id,
                        index,
                        str(line.get("speaker", "")),
                        str(line.get("text", "")),
                        float(line.get("start", 0)),
                        float(line.get("end", 0)),
                    )
                    for index, line in enumerate(transcript)
                ],
            )


def load_job(job_id: str, db_path: Path | None = None) -> dict[str, Any] | None:
    with connect(db_path) as db:
        row = db.execute(
            "SELECT * FROM transcription_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        if row is None:
            return None

        lines = db.execute(
            """
            SELECT speaker, text, start, end
            FROM transcript_lines
            WHERE job_id = ?
            ORDER BY line_index
            """,
            (job_id,),
        ).fetchall()

    job = dict(row)
    telemetry = _from_json(job.pop("telemetry_json", None))
    job["telemetry"] = telemetry or {}
    job["cancellation_requested"] = bool(job.get("cancellation_requested"))
    job["transcript"] = [dict(line) for line in lines] if lines else None
    return job


def load_jobs(db_path: Path | None = None) -> dict[str, dict[str, Any]]:
    with connect(db_path) as db:
        job_ids = [
            row["job_id"]
            for row in db.execute(
                "SELECT job_id FROM transcription_jobs ORDER BY created_at"
            ).fetchall()
        ]

    return {
        job_id: job
        for job_id in job_ids
        if (job := load_job(job_id, db_path=db_path)) is not None
    }


def mark_interrupted_jobs(db_path: Path | None = None) -> None:
    now = time.time()
    with connect(db_path) as db:
        db.execute(
            """
            UPDATE transcription_jobs
            SET status = 'failed',
                progress = CASE WHEN progress >= 100 THEN progress ELSE 0 END,
                message = 'Transkription wurde durch Backend-Neustart unterbrochen',
                error = 'Transkription wurde durch Backend-Neustart unterbrochen',
                updated_at = ?
            WHERE status IN ('pending', 'processing')
            """,
            (now,),
        )


def save_session(
    session_id: str,
    state: dict[str, Any],
    db_path: Path | None = None,
) -> dict[str, Any]:
    now = time.time()
    with connect(db_path) as db:
        existing = db.execute(
            "SELECT created_at FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        created_at = float(existing["created_at"]) if existing else now

        db.execute(
            """
            INSERT INTO sessions (
                session_id, job_id, current_step, skipped_assignment, export_metadata_json,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                job_id = excluded.job_id,
                current_step = excluded.current_step,
                skipped_assignment = excluded.skipped_assignment,
                export_metadata_json = excluded.export_metadata_json,
                updated_at = excluded.updated_at
            """,
            (
                session_id,
                state.get("job_id"),
                state.get("current_step"),
                1 if state.get("skipped_assignment") else 0,
                _to_json(state.get("export_metadata") or {}),
                created_at,
                now,
            ),
        )

        if state.get("job_id"):
            db.execute(
                """
                UPDATE transcription_jobs
                SET session_id = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (session_id, now, state["job_id"]),
            )

        db.execute("DELETE FROM tops WHERE session_id = ?", (session_id,))
        db.executemany(
            "INSERT INTO tops (session_id, top_index, title) VALUES (?, ?, ?)",
            [
                (session_id, index, str(title))
                for index, title in enumerate(state.get("tops") or [])
            ],
        )

        db.execute("DELETE FROM assignments WHERE session_id = ?", (session_id,))
        db.executemany(
            """
            INSERT INTO assignments (session_id, line_index, top_index)
            VALUES (?, ?, ?)
            """,
            [
                (session_id, index, assignment)
                for index, assignment in enumerate(state.get("assignments") or [])
            ],
        )

        db.execute("DELETE FROM speaker_names WHERE session_id = ?", (session_id,))
        db.executemany(
            """
            INSERT INTO speaker_names (session_id, speaker_id, display_name)
            VALUES (?, ?, ?)
            """,
            [
                (session_id, str(speaker_id), str(display_name))
                for speaker_id, display_name in (
                    state.get("speaker_names") or {}
                ).items()
            ],
        )

        db.execute("DELETE FROM summaries WHERE session_id = ?", (session_id,))
        db.executemany(
            "INSERT INTO summaries (session_id, top_index, summary) VALUES (?, ?, ?)",
            [
                (session_id, int(top_index), str(summary))
                for top_index, summary in (state.get("summaries") or {}).items()
            ],
        )

        db.execute("DELETE FROM summary_reviews WHERE session_id = ?", (session_id,))
        db.executemany(
            """
            INSERT INTO summary_reviews (session_id, top_index, review_json)
            VALUES (?, ?, ?)
            """,
            [
                (session_id, int(top_index), _to_json(review) or "{}")
                for top_index, review in (state.get("summary_reviews") or {}).items()
            ],
        )

        if state.get("transcript") is not None:
            db.execute(
                "DELETE FROM session_transcript_lines WHERE session_id = ?",
                (session_id,),
            )
            db.executemany(
                """
                INSERT INTO session_transcript_lines (
                    session_id, line_index, speaker, text, start, end
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        session_id,
                        index,
                        str(line.get("speaker", "")),
                        str(line.get("text", "")),
                        float(line.get("start", 0)),
                        float(line.get("end", 0)),
                    )
                    for index, line in enumerate(state.get("transcript") or [])
                ],
            )

    return load_session(session_id, db_path=db_path) or {}


def load_session(
    session_id: str,
    db_path: Path | None = None,
) -> dict[str, Any] | None:
    with connect(db_path) as db:
        row = db.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if row is None:
            return None

        tops = db.execute(
            """
            SELECT title FROM tops
            WHERE session_id = ?
            ORDER BY top_index
            """,
            (session_id,),
        ).fetchall()
        assignments = db.execute(
            """
            SELECT line_index, top_index FROM assignments
            WHERE session_id = ?
            ORDER BY line_index
            """,
            (session_id,),
        ).fetchall()
        speaker_names = db.execute(
            """
            SELECT speaker_id, display_name FROM speaker_names
            WHERE session_id = ?
            ORDER BY speaker_id
            """,
            (session_id,),
        ).fetchall()
        summaries = db.execute(
            """
            SELECT top_index, summary FROM summaries
            WHERE session_id = ?
            ORDER BY top_index
            """,
            (session_id,),
        ).fetchall()
        summary_reviews = db.execute(
            """
            SELECT top_index, review_json FROM summary_reviews
            WHERE session_id = ?
            ORDER BY top_index
            """,
            (session_id,),
        ).fetchall()
        transcript = db.execute(
            """
            SELECT speaker, text, start, end
            FROM session_transcript_lines
            WHERE session_id = ?
            ORDER BY line_index
            """,
            (session_id,),
        ).fetchall()

    session = dict(row)
    session["skipped_assignment"] = bool(session["skipped_assignment"])
    session["tops"] = [item["title"] for item in tops]
    session["assignments"] = [item["top_index"] for item in assignments]
    session["speaker_names"] = {
        item["speaker_id"]: item["display_name"] for item in speaker_names
    }
    session["summaries"] = {
        int(item["top_index"]): item["summary"] for item in summaries
    }
    session["summary_reviews"] = {
        int(item["top_index"]): _from_json(item["review_json"]) or {}
        for item in summary_reviews
    }
    session["export_metadata"] = _from_json(session.get("export_metadata_json")) or {}
    session["transcript"] = [dict(line) for line in transcript] if transcript else None
    return session
