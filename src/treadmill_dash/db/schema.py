"""Database schema and migrations."""

from __future__ import annotations

import aiosqlite

SCHEMA_VERSION = 1

SCHEMA_SQL = """
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


async def init_db(db: aiosqlite.Connection) -> None:
    """Create tables if they don't exist."""
    await db.executescript(SCHEMA_SQL)

    # Set schema version if not present
    async with db.execute("SELECT COUNT(*) FROM schema_version") as cur:
        row = await cur.fetchone()
        if row and row[0] == 0:
            await db.execute(
                "INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,)
            )
            await db.commit()
