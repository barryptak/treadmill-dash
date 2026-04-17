"""Data access layer for treadmill sessions."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, date
from pathlib import Path
from typing import Optional

import aiosqlite

from treadmill_dash.db.schema import init_db
from treadmill_dash.models import TreadmillData, MeetingStats

# Default DB location: ~/.treadmill-dash/data.db
DEFAULT_DB_PATH = Path.home() / ".treadmill-dash" / "data.db"


@dataclass
class SessionRecord:
    """A persisted session."""

    id: int
    start_time: datetime
    end_time: Optional[datetime]
    total_distance_m: float
    avg_speed_kmh: float
    max_speed_kmh: float
    calories: int
    elapsed_s: int
    sample_count: int

    @property
    def distance_km(self) -> float:
        return self.total_distance_m / 1000

    @property
    def elapsed_fmt(self) -> str:
        m, s = divmod(self.elapsed_s, 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    @property
    def date_str(self) -> str:
        return self.start_time.strftime("%Y-%m-%d %H:%M")


@dataclass
class LifetimeStats:
    """Aggregated all-time statistics."""

    total_sessions: int = 0
    total_distance_m: float = 0.0
    total_elapsed_s: int = 0
    total_calories: int = 0
    max_speed_kmh: float = 0.0
    longest_session_s: int = 0
    longest_distance_m: float = 0.0

    @property
    def distance_km(self) -> float:
        return self.total_distance_m / 1000

    @property
    def elapsed_fmt(self) -> str:
        m, s = divmod(self.total_elapsed_s, 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}"


@dataclass
class TodayStats:
    """Aggregated stats for today."""

    sessions: int = 0
    distance_m: float = 0.0
    elapsed_s: int = 0
    calories: int = 0

    @property
    def distance_km(self) -> float:
        return self.distance_m / 1000

    @property
    def elapsed_fmt(self) -> str:
        m, s = divmod(self.elapsed_s, 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}"


class Repository:
    """Async data access for treadmill sessions."""

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH):
        self._db_path = Path(db_path)
        self._db: Optional[aiosqlite.Connection] = None

    async def open(self) -> None:
        """Open the database and ensure schema exists."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self._db_path))
        self._db.row_factory = aiosqlite.Row
        await init_db(self._db)

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    # -- Sessions --

    async def try_resume_session(
        self, treadmill_distance_m: float, treadmill_elapsed_s: int
    ) -> Optional[int]:
        """Check if the treadmill is continuing a previous session.

        Returns the session ID to resume, or None if this is a new session.

        The treadmill's cumulative counters (distance, elapsed) only go up
        within a single treadmill session.  If the current treadmill values
        are ≥ the most recent DB session's values, it's the same treadmill
        session — even if the app was quit and restarted in between.
        If either counter is lower, the treadmill was reset → new session.

        Also checks wall-clock time: if the most recent session ended more
        than 2 hours ago, it's definitely a different treadmill session
        (no treadmill runs that long unattended).
        """
        assert self._db
        async with self._db.execute(
            """SELECT id, total_distance_m, elapsed_s, end_time, start_time
            FROM sessions
            ORDER BY COALESCE(end_time, start_time) DESC LIMIT 1"""
        ) as cur:
            row = await cur.fetchone()

        if row is None:
            return None

        prev_dist = row["total_distance_m"]
        prev_elapsed = row["elapsed_s"]

        # Don't resume an empty session (garbage from a previous reset)
        if prev_dist == 0 and prev_elapsed == 0 and treadmill_distance_m == 0 and treadmill_elapsed_s == 0:
            return None

        # Treadmill counters lower than stored → new treadmill session
        if treadmill_distance_m < prev_dist or treadmill_elapsed_s < prev_elapsed:
            return None

        # Wall-clock check: if the session ended long ago, it can't be
        # the same treadmill session even if counters happen to be higher
        last_time_str = row["end_time"] or row["start_time"]
        if last_time_str:
            last_time = datetime.fromisoformat(last_time_str)
            gap = (datetime.now() - last_time).total_seconds()
            if gap > 7200:  # 2 hours
                return None

        # Same or higher and recent → same treadmill session, resume
        return row["id"]

    async def start_session(self) -> int:
        """Create a new session row and return its ID."""
        assert self._db
        now = datetime.now().isoformat()
        async with self._db.execute(
            "INSERT INTO sessions (start_time) VALUES (?)", (now,)
        ) as cur:
            session_id = cur.lastrowid
        await self._db.commit()
        return session_id  # type: ignore[return-value]

    async def get_session_max_speed(self, session_id: int) -> float:
        """Get the max speed recorded for a session (for resuming)."""
        assert self._db
        async with self._db.execute(
            "SELECT max_speed_kmh FROM sessions WHERE id = ?", (session_id,)
        ) as cur:
            row = await cur.fetchone()
        return row["max_speed_kmh"] if row else 0.0

    async def end_session(
        self,
        session_id: int,
        total_distance_m: float,
        avg_speed_kmh: float,
        max_speed_kmh: float,
        calories: int,
        elapsed_s: int,
        sample_count: int,
    ) -> None:
        """Finalize a session with summary stats."""
        assert self._db
        now = datetime.now().isoformat()
        await self._db.execute(
            """UPDATE sessions SET
                end_time = ?, total_distance_m = ?, avg_speed_kmh = ?,
                max_speed_kmh = ?, calories = ?, elapsed_s = ?, sample_count = ?
            WHERE id = ?""",
            (now, total_distance_m, avg_speed_kmh, max_speed_kmh, calories,
             elapsed_s, sample_count, session_id),
        )
        await self._db.commit()

    # -- Samples --

    async def add_sample(self, session_id: int, data: TreadmillData) -> None:
        """Record a single data sample."""
        assert self._db
        now = datetime.now().isoformat()
        await self._db.execute(
            """INSERT INTO samples
                (session_id, timestamp, speed_kmh, total_distance_m,
                 inclination_pct, heart_rate_bpm, calories, elapsed_s)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                now,
                data.instantaneous_speed_kmh,
                data.total_distance_m,
                data.inclination_pct,
                data.heart_rate_bpm,
                data.total_energy_kcal,
                data.elapsed_time_s,
            ),
        )
        # Commit in batches — the caller can flush periodically
        # We commit here for safety; optimize later if needed
        await self._db.commit()

    # -- Queries --

    async def get_recent_sessions(self, limit: int = 10) -> list[SessionRecord]:
        """Get the N most recent completed sessions."""
        assert self._db
        async with self._db.execute(
            """SELECT id, start_time, end_time, total_distance_m, avg_speed_kmh,
                      max_speed_kmh, calories, elapsed_s, sample_count
            FROM sessions WHERE end_time IS NOT NULL AND sample_count > 0
            ORDER BY start_time DESC LIMIT ?""",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
        return [
            SessionRecord(
                id=r["id"],
                start_time=datetime.fromisoformat(r["start_time"]),
                end_time=datetime.fromisoformat(r["end_time"]) if r["end_time"] else None,
                total_distance_m=r["total_distance_m"],
                avg_speed_kmh=r["avg_speed_kmh"],
                max_speed_kmh=r["max_speed_kmh"],
                calories=r["calories"],
                elapsed_s=r["elapsed_s"],
                sample_count=r["sample_count"],
            )
            for r in rows
        ]

    async def get_lifetime_stats(self, exclude_session_id: Optional[int] = None) -> LifetimeStats:
        """Compute all-time aggregate stats, optionally excluding one session."""
        assert self._db
        if exclude_session_id is not None:
            query = """SELECT
                COUNT(*) as cnt,
                COALESCE(SUM(total_distance_m), 0) as dist,
                COALESCE(SUM(elapsed_s), 0) as elapsed,
                COALESCE(SUM(calories), 0) as cals,
                COALESCE(MAX(max_speed_kmh), 0) as max_spd,
                COALESCE(MAX(elapsed_s), 0) as longest_s,
                COALESCE(MAX(total_distance_m), 0) as longest_d
            FROM sessions WHERE end_time IS NOT NULL AND sample_count > 0
              AND id != ?"""
            args: tuple = (exclude_session_id,)
        else:
            query = """SELECT
                COUNT(*) as cnt,
                COALESCE(SUM(total_distance_m), 0) as dist,
                COALESCE(SUM(elapsed_s), 0) as elapsed,
                COALESCE(SUM(calories), 0) as cals,
                COALESCE(MAX(max_speed_kmh), 0) as max_spd,
                COALESCE(MAX(elapsed_s), 0) as longest_s,
                COALESCE(MAX(total_distance_m), 0) as longest_d
            FROM sessions WHERE end_time IS NOT NULL AND sample_count > 0"""
            args = ()
        async with self._db.execute(query, args) as cur:
            r = await cur.fetchone()
        if not r or r["cnt"] == 0:
            return LifetimeStats()
        return LifetimeStats(
            total_sessions=r["cnt"],
            total_distance_m=r["dist"],
            total_elapsed_s=r["elapsed"],
            total_calories=r["cals"],
            max_speed_kmh=r["max_spd"],
            longest_session_s=r["longest_s"],
            longest_distance_m=r["longest_d"],
        )

    async def get_today_stats(self, exclude_session_id: Optional[int] = None) -> TodayStats:
        """Compute today's aggregate stats, optionally excluding one session."""
        assert self._db
        today = date.today().isoformat()
        if exclude_session_id is not None:
            query = """SELECT
                COUNT(*) as cnt,
                COALESCE(SUM(total_distance_m), 0) as dist,
                COALESCE(SUM(elapsed_s), 0) as elapsed,
                COALESCE(SUM(calories), 0) as cals
            FROM sessions
            WHERE end_time IS NOT NULL AND sample_count > 0
              AND start_time >= ? AND id != ?"""
            args: tuple = (today, exclude_session_id)
        else:
            query = """SELECT
                COUNT(*) as cnt,
                COALESCE(SUM(total_distance_m), 0) as dist,
                COALESCE(SUM(elapsed_s), 0) as elapsed,
                COALESCE(SUM(calories), 0) as cals
            FROM sessions
            WHERE end_time IS NOT NULL AND sample_count > 0
              AND start_time >= ?"""
            args = (today,)
        async with self._db.execute(query, args) as cur:
            r = await cur.fetchone()
        if not r or r["cnt"] == 0:
            return TodayStats()
        return TodayStats(
            sessions=r["cnt"],
            distance_m=r["dist"],
            elapsed_s=r["elapsed"],
            calories=r["cals"],
        )

    # -- Export --

    async def save_meeting(
        self,
        session_id: Optional[int],
        meeting: MeetingStats,
    ) -> int:
        """Persist a completed meeting's treadmill stats."""
        assert self._db
        end_time = datetime.now().isoformat()
        async with self._db.execute(
            """INSERT INTO meetings
                (session_id, meeting_name, start_time, end_time,
                 distance_m, elapsed_s, calories)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                meeting.meeting_name,
                meeting.start_time.isoformat(),
                end_time,
                meeting.distance_m,
                meeting.elapsed_s,
                meeting.calories,
            ),
        ) as cur:
            meeting_id = cur.lastrowid
        await self._db.commit()
        return meeting_id  # type: ignore[return-value]

    # -- Export --

    async def export_session_csv(self, session_id: int) -> str:
        """Export a session's samples as CSV text."""
        assert self._db
        lines = ["timestamp,speed_kmh,total_distance_m,inclination_pct,heart_rate_bpm,calories,elapsed_s"]
        async with self._db.execute(
            """SELECT timestamp, speed_kmh, total_distance_m, inclination_pct,
                      heart_rate_bpm, calories, elapsed_s
            FROM samples WHERE session_id = ? ORDER BY timestamp""",
            (session_id,),
        ) as cur:
            async for row in cur:
                vals = [
                    row["timestamp"],
                    str(row["speed_kmh"]),
                    str(row["total_distance_m"] or ""),
                    str(row["inclination_pct"] or ""),
                    str(row["heart_rate_bpm"] or ""),
                    str(row["calories"] or ""),
                    str(row["elapsed_s"] or ""),
                ]
                lines.append(",".join(vals))
        return "\n".join(lines)
