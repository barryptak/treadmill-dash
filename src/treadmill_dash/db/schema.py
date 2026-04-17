"""Database schema and migrations."""

from __future__ import annotations

import aiosqlite

SCHEMA_VERSION = 2

# Base schema (v1) — sessions + samples
_V1_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    start_time      TEXT NOT NULL,           -- ISO-8601
    end_time        TEXT,                    -- ISO-8601, NULL if still active
    total_distance_m REAL NOT NULL DEFAULT 0,
    avg_speed_kmh   REAL NOT NULL DEFAULT 0,
    max_speed_kmh   REAL NOT NULL DEFAULT 0,
    calories        INTEGER NOT NULL DEFAULT 0,
    elapsed_s       INTEGER NOT NULL DEFAULT 0,
    sample_count    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS samples (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER NOT NULL REFERENCES sessions(id),
    timestamp       TEXT NOT NULL,           -- ISO-8601
    speed_kmh       REAL NOT NULL DEFAULT 0,
    total_distance_m REAL,
    inclination_pct REAL,
    heart_rate_bpm  INTEGER,
    calories        INTEGER,
    elapsed_s       INTEGER
);

CREATE INDEX IF NOT EXISTS idx_samples_session ON samples(session_id);
CREATE INDEX IF NOT EXISTS idx_sessions_start ON sessions(start_time);

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);
"""

# v1 → v2: meetings table
_V2_SQL = """
CREATE TABLE IF NOT EXISTS meetings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER REFERENCES sessions(id),
    meeting_name    TEXT NOT NULL,
    start_time      TEXT NOT NULL,           -- ISO-8601
    end_time        TEXT,                    -- ISO-8601
    distance_m      REAL NOT NULL DEFAULT 0,
    elapsed_s       INTEGER NOT NULL DEFAULT 0,
    calories        INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_meetings_name ON meetings(meeting_name);
CREATE INDEX IF NOT EXISTS idx_meetings_start ON meetings(start_time);
CREATE INDEX IF NOT EXISTS idx_meetings_session ON meetings(session_id);
"""


async def _get_version(db: aiosqlite.Connection) -> int:
    """Get the current schema version, or 0 if table doesn't exist."""
    try:
        async with db.execute("SELECT version FROM schema_version LIMIT 1") as cur:
            row = await cur.fetchone()
            return row[0] if row else 0
    except Exception:
        return 0


async def init_db(db: aiosqlite.Connection) -> None:
    """Create tables and run any pending migrations."""
    # Ensure base schema exists
    await db.executescript(_V1_SQL)

    version = await _get_version(db)

    if version == 0:
        # Fresh DB — apply all migrations and set version
        await db.executescript(_V2_SQL)
        await db.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
        await db.commit()
        return

    # Incremental migrations
    if version < 2:
        await db.executescript(_V2_SQL)

    # Update stored version
    if version < SCHEMA_VERSION:
        await db.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))
        await db.commit()
