"""Generate HTML table representations of treadmill stats for clipboard."""

from __future__ import annotations

from treadmill_dash.config import Config
from treadmill_dash.db.repository import LifetimeStats, TodayStats
from treadmill_dash.models import SessionStats, MeetingStats

_HDR_BG = "#5B5FC7"


def _fmt_time(seconds: int) -> str:
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _fmt_dist(meters: float, use_miles: bool) -> str:
    val = meters / 1609.344 if use_miles else meters / 1000
    unit = "mi" if use_miles else "km"
    return f"{val:.2f} {unit}"


def _make_table(title: str, rows: list[tuple[str, str]]) -> tuple[str, str]:
    """Build an HTML table and a plain-text equivalent."""
    # --- HTML ---
    tr = []
    tr.append(
        f'<tr><td colspan="2" style="padding:5px 12px;background:{_HDR_BG};'
        f'color:white;font-weight:600;">{title}</td></tr>'
    )
    for label, value in rows:
        tr.append(
            f'<tr>'
            f'<td style="padding:3px 12px;border-bottom:1px solid #eee;color:#555;">'
            f'{label}</td>'
            f'<td style="padding:3px 12px;border-bottom:1px solid #eee;'
            f'font-weight:500;">{value}</td>'
            f'</tr>'
        )
    html = (
        '<table style="border-collapse:collapse;font-family:\'Segoe UI\','
        "sans-serif;font-size:13px;border:1px solid #ddd;\">"
        + "".join(tr)
        + "</table>"
    )

    # --- Plain text ---
    max_label = max(len(l) for l, _ in rows) if rows else 0
    lines = [title]
    for label, value in rows:
        lines.append(f"  {label:<{max_label + 2}}{value}")
    return html, "\n".join(lines)


def current_session_html(
    session: SessionStats, config: Config
) -> tuple[str, str]:
    """HTML + plain text for the current treadmill session."""
    mi = config.use_miles
    elapsed = session.last_elapsed_s or int(session.duration_s)
    rows = [
        ("Duration", _fmt_time(elapsed)),
        ("Distance", _fmt_dist(session.total_distance_m, mi)),
        ("Calories", f"{session.total_energy_kcal} kcal"),
    ]
    return _make_table("🏃 Current Session", rows)


def today_stats_html(
    today: TodayStats, session: SessionStats, config: Config
) -> tuple[str, str]:
    """HTML + plain text for today's totals (DB + live session)."""
    mi = config.use_miles
    cur_elapsed = session.last_elapsed_s or int(session.duration_s)
    has_live = session.sample_count > 0
    rows = [
        ("Sessions", str(today.sessions + (1 if has_live else 0))),
        ("Distance", _fmt_dist(today.distance_m + session.total_distance_m, mi)),
        ("Time", _fmt_time(today.elapsed_s + cur_elapsed)),
        ("Calories", f"{today.calories + session.total_energy_kcal} kcal"),
    ]
    return _make_table("📅 Today's Walking", rows)


def lifetime_stats_html(
    lifetime: LifetimeStats, session: SessionStats, config: Config
) -> tuple[str, str]:
    """HTML + plain text for all-time totals (DB + live session)."""
    mi = config.use_miles
    cur_elapsed = session.last_elapsed_s or int(session.duration_s)
    has_live = session.sample_count > 0
    rows = [
        ("Sessions", str(lifetime.total_sessions + (1 if has_live else 0))),
        ("Distance", _fmt_dist(lifetime.total_distance_m + session.total_distance_m, mi)),
        ("Time", _fmt_time(lifetime.total_elapsed_s + cur_elapsed)),
        ("Calories", f"{lifetime.total_calories + session.total_energy_kcal} kcal"),
    ]
    return _make_table("📊 Lifetime Stats", rows)


def meeting_stats_html(
    meeting: MeetingStats, config: Config
) -> tuple[str, str]:
    """HTML + plain text for the current meeting's treadmill stats."""
    mi = config.use_miles
    rows = [
        ("Duration", _fmt_time(meeting.elapsed_s)),
        ("Distance", _fmt_dist(meeting.distance_m, mi)),
        ("Calories", f"{meeting.calories} kcal"),
    ]
    return _make_table(f"📞 {meeting.meeting_name}", rows)
