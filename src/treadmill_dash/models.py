"""Data models for treadmill telemetry."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class TreadmillData:
    """Parsed snapshot from a single FTMS Treadmill Data notification."""

    # Always present
    instantaneous_speed_kmh: float = 0.0  # km/h

    # Optional fields (depend on flags)
    average_speed_kmh: Optional[float] = None
    total_distance_m: Optional[float] = None
    inclination_pct: Optional[float] = None
    ramp_angle_deg: Optional[float] = None
    positive_elevation_gain_m: Optional[float] = None
    negative_elevation_gain_m: Optional[float] = None
    instantaneous_pace_min_per_km: Optional[float] = None
    average_pace_min_per_km: Optional[float] = None
    total_energy_kcal: Optional[int] = None
    energy_per_hour_kcal: Optional[int] = None
    energy_per_minute_kcal: Optional[int] = None
    heart_rate_bpm: Optional[int] = None
    metabolic_equivalent: Optional[float] = None
    elapsed_time_s: Optional[int] = None
    remaining_time_s: Optional[int] = None

    # Derived / display helpers
    @property
    def speed_mph(self) -> float:
        return self.instantaneous_speed_kmh * 0.621371

    @property
    def distance_miles(self) -> Optional[float]:
        if self.total_distance_m is not None:
            return self.total_distance_m / 1609.344
        return None

    @property
    def elapsed_time_fmt(self) -> str:
        if self.elapsed_time_s is None:
            return "--:--"
        m, s = divmod(self.elapsed_time_s, 60)
        h, m = divmod(m, 60)
        if h:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"


@dataclass
class MeetingStats:
    """Transient stats tracked while in a Teams meeting."""

    meeting_name: str = ""
    start_time: datetime = field(default_factory=datetime.now)
    start_distance_m: float = 0.0
    start_elapsed_s: int = 0
    start_calories: int = 0

    # Snapshot of treadmill values when meeting started — deltas give
    # per-meeting stats.  The _banked_* fields accumulate stats from
    # previous treadmill sessions within the same meeting (when the
    # treadmill resets mid-meeting).
    distance_m: float = 0.0
    elapsed_s: int = 0
    calories: int = 0
    _banked_distance_m: float = 0.0
    _banked_elapsed_s: int = 0
    _banked_calories: int = 0

    def update(self, session: "SessionStats") -> None:
        """Recompute meeting-relative stats from current session values."""
        self.distance_m = self._banked_distance_m + max(0, session.total_distance_m - self.start_distance_m)
        cur_elapsed = session.last_elapsed_s or int(session.duration_s)
        self.elapsed_s = self._banked_elapsed_s + max(0, cur_elapsed - self.start_elapsed_s)
        self.calories = self._banked_calories + max(0, session.total_energy_kcal - self.start_calories)

    def rebase(self, session: "SessionStats") -> None:
        """Bank accumulated stats and reset start counters for a new treadmill session.

        Call this just before the session object is replaced after a
        treadmill reset, so the current deltas are preserved.
        """
        self.update(session)
        self._banked_distance_m = self.distance_m
        self._banked_elapsed_s = self.elapsed_s
        self._banked_calories = self.calories
        self.start_distance_m = 0.0
        self.start_elapsed_s = 0
        self.start_calories = 0

    @property
    def elapsed_fmt(self) -> str:
        m, s = divmod(self.elapsed_s, 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


@dataclass
class SessionStats:
    """Aggregated stats for the current treadmill session.

    Uses raw treadmill-reported values (distance, elapsed, calories) as the
    source of truth.  The app may disconnect and reconnect mid-session; the
    treadmill's cumulative counters are authoritative.
    """

    start_time: datetime = field(default_factory=datetime.now)
    sample_count: int = 0
    max_speed_kmh: float = 0.0
    speed_sum: float = 0.0  # for computing average (only while moving)
    moving_sample_count: int = 0  # samples where speed > 0

    # Raw treadmill values — always latest reported
    total_distance_m: float = 0.0
    total_energy_kcal: int = 0
    last_elapsed_s: int = 0

    def update(self, data: TreadmillData) -> None:
        """Incorporate a new data sample."""
        self.sample_count += 1

        if data.instantaneous_speed_kmh > 0:
            self.max_speed_kmh = max(self.max_speed_kmh, data.instantaneous_speed_kmh)
            self.speed_sum += data.instantaneous_speed_kmh
            self.moving_sample_count += 1

        if data.total_distance_m is not None:
            self.total_distance_m = data.total_distance_m
        if data.total_energy_kcal is not None:
            self.total_energy_kcal = data.total_energy_kcal
        if data.elapsed_time_s is not None:
            self.last_elapsed_s = data.elapsed_time_s

    @property
    def avg_speed_kmh(self) -> float:
        if self.moving_sample_count == 0:
            return 0.0
        return self.speed_sum / self.moving_sample_count

    @property
    def duration_s(self) -> float:
        return (datetime.now() - self.start_time).total_seconds()

    @property
    def elapsed_time_fmt(self) -> str:
        elapsed = self.last_elapsed_s or int(self.duration_s)
        m, s = divmod(elapsed, 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    def summary(self) -> str:
        dist_km = self.total_distance_m / 1000
        dist_mi = self.total_distance_m / 1609.344
        elapsed = self.last_elapsed_s or int(self.duration_s)
        m, s = divmod(elapsed, 60)
        h, m = divmod(m, 60)
        time_str = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

        lines = [
            "┌─────────────────────────────────┐",
            "│       SESSION SUMMARY           │",
            "├─────────────────────────────────┤",
            f"│  Duration:    {time_str:>16s}  │",
            f"│  Distance:    {dist_km:>10.2f} km     │",
            f"│              ({dist_mi:>7.2f} mi)     │",
            f"│  Avg speed:   {self.avg_speed_kmh:>10.1f} km/h  │",
            f"│  Max speed:   {self.max_speed_kmh:>10.1f} km/h  │",
            f"│  Calories:    {self.total_energy_kcal:>10d} kcal  │",
            "└─────────────────────────────────┘",
        ]
        return "\n".join(lines)
