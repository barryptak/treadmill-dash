"""Textual TUI dashboard for live treadmill data."""

from __future__ import annotations

import asyncio
from typing import Optional

from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Footer, Header, Static, DataTable

from treadmill_dash.models import TreadmillData, SessionStats, MeetingStats
from treadmill_dash.config import Config
from treadmill_dash.clipboard import copy_html_to_clipboard
from treadmill_dash.html_stats import (
    current_session_html,
    today_stats_html,
    lifetime_stats_html,
    meeting_stats_html,
)
from treadmill_dash.teams.meeting_detector import get_active_meeting


class BigStat(Static):
    """A large stat display widget."""

    value: reactive[str] = reactive("--")
    label: reactive[str] = reactive("")
    unit: reactive[str] = reactive("")

    def __init__(self, label: str = "", unit: str = "", **kwargs) -> None:
        super().__init__(**kwargs)
        self.label = label
        self.unit = unit

    def render(self) -> str:
        return f"[dim]{self.label}[/]\n[bold]{self.value}[/] [dim]{self.unit}[/]"


class SpeedGauge(Static):
    """Large speed display — the hero widget."""

    speed: reactive[float] = reactive(0.0)
    unit: reactive[str] = reactive("km/h")

    def render(self) -> str:
        s = self.speed
        bar_len = int(min(s / 10.0, 1.0) * 30)
        bar = "█" * bar_len + "░" * (30 - bar_len)
        return (
            f"[dim]SPEED[/]\n"
            f"[bold cyan]{s:5.1f}[/] [dim]{self.unit}[/]\n"
            f"[cyan]{bar}[/]"
        )


class ConnectionStatus(Static):
    """Shows BLE connection state."""

    status: reactive[str] = reactive("Disconnected")

    def render(self) -> str:
        if "Connected" in self.status or "Receiving" in self.status:
            return f"[green]● {self.status}[/]"
        elif "Connecting" in self.status or "Scanning" in self.status or "Reconnect" in self.status:
            return f"[yellow]◌ {self.status}[/]"
        else:
            return f"[red]○ {self.status}[/]"


# ---------------------------------------------------------------------------
# Stats Screen — shows lifetime, today, recent sessions & personal bests
# ---------------------------------------------------------------------------

class StatsScreen(Screen):
    """History & stats screen (press 's' to open, Escape to close)."""

    BINDINGS = [
        ("escape", "app.pop_screen", "Back"),
        ("u", "toggle_units", "Toggle km/mph"),
        ("s", "app.pop_screen", "Back"),
    ]

    CSS = """
    StatsScreen {
        layout: vertical;
    }

    #stats-current {
        height: 5;
        padding: 1 0;
        margin: 0 1;
        background: $boost;
    }
    #stats-current > Static {
        width: 1fr;
        padding: 0 2;
    }

    #stats-lifetime {
        height: 5;
        padding: 1 0;
        margin: 0 1;
        background: $surface-darken-1;
    }
    #stats-lifetime > Static {
        width: 1fr;
        padding: 0 2;
    }

    #stats-today {
        height: 5;
        padding: 1 0;
        margin: 0 1;
    }
    #stats-today > Static {
        width: 1fr;
        padding: 0 2;
    }

    #stats-bests {
        height: 5;
        padding: 1 0;
        margin: 0 1;
        background: $surface-darken-1;
    }
    #stats-bests > Static {
        width: 1fr;
        padding: 0 2;
    }

    #recent-table {
        margin: 0 1;
        height: 1fr;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("[bold]  🏃 CURRENT SESSION[/]", id="current-hdr")
        with Horizontal(id="stats-current"):
            yield BigStat(label="💨 SPEED", id="cs-speed")
            yield BigStat(label="📏 DISTANCE", id="cs-distance")
            yield BigStat(label="⏱️ TIME", id="cs-time")
            yield BigStat(label="🔥 CALORIES", unit="kcal", id="cs-calories")
        yield Static("[bold]  📊 LIFETIME (incl. current)[/]", id="lifetime-hdr")
        with Horizontal(id="stats-lifetime"):
            yield BigStat(label="🔢 SESSIONS", id="lt-sessions")
            yield BigStat(label="📏 DISTANCE", id="lt-distance")
            yield BigStat(label="⏱️ TIME", id="lt-time")
            yield BigStat(label="🔥 CALORIES", unit="kcal", id="lt-calories")
        yield Static("[bold]  📅 TODAY (incl. current)[/]", id="today-hdr")
        with Horizontal(id="stats-today"):
            yield BigStat(label="🔢 SESSIONS", id="td-sessions")
            yield BigStat(label="📏 DISTANCE", id="td-distance")
            yield BigStat(label="⏱️ TIME", id="td-time")
            yield BigStat(label="🔥 CALORIES", unit="kcal", id="td-calories")
        yield Static("[bold]  🏆 PERSONAL BESTS[/]", id="bests-hdr")
        with Horizontal(id="stats-bests"):
            yield BigStat(label="🚀 MAX SPEED", id="pb-speed")
            yield BigStat(label="⏳ LONGEST SESSION", id="pb-duration")
            yield BigStat(label="🥇 FARTHEST WALK", id="pb-distance")
        yield Static("[bold]  🕐 RECENT SESSIONS[/]", id="recent-hdr")
        yield DataTable(id="recent-table", cursor_type="none")
        yield Footer()

    async def on_mount(self) -> None:
        self.title = "📊 Stats & History"
        self._apply_units()
        await self._load_db_stats()
        self._refresh_timer = self.set_interval(1.0, self._refresh_live)

    def _get_app(self) -> "TreadmillDashboard":
        return self.app  # type: ignore[return-value]

    def _apply_units(self) -> None:
        """Set unit labels based on current config."""
        app = self._get_app()
        su = app.config.speed_unit
        du = app.config.distance_unit
        self.query_one("#cs-speed", BigStat).unit = su
        self.query_one("#cs-distance", BigStat).unit = du
        self.query_one("#lt-distance", BigStat).unit = du
        self.query_one("#td-distance", BigStat).unit = du
        self.query_one("#pb-speed", BigStat).unit = su
        self.query_one("#pb-distance", BigStat).unit = du

    def action_toggle_units(self) -> None:
        app = self._get_app()
        app.config.use_miles = not app.config.use_miles
        self._apply_units()
        self._rebuild_table()

    def _rebuild_table(self) -> None:
        """Re-render the recent sessions table with current units.

        The live/active session is shown as the first row (if moving) and
        is updated every second by _refresh_live.
        """
        app = self._get_app()
        use_miles = app.config.use_miles
        du = app.config.distance_unit
        su = app.config.speed_unit
        table = self.query_one("#recent-table", DataTable)
        table.clear(columns=True)
        table.add_columns("📅 Started", "⏱️ Duration", f"📏 Dist ({du})", f"⚡ Avg ({su})", f"🚀 Max ({su})", "🔥 Cals")

        # Live session row (always first)
        session = app.session
        if session.sample_count > 0:
            dist = session.total_distance_m / (1609.344 if use_miles else 1000)
            avg = session.avg_speed_kmh * (0.621371 if use_miles else 1)
            mx = session.max_speed_kmh * (0.621371 if use_miles else 1)
            table.add_row(
                "[bold green]▶ LIVE[/]",
                f"[bold green]{session.elapsed_time_fmt}[/]",
                f"[bold green]{dist:.2f}[/]",
                f"[bold green]{avg:.1f}[/]",
                f"[bold green]{mx:.1f}[/]",
                f"[bold green]{session.total_energy_kcal}[/]",
                key="live",
            )

        # Past sessions from DB (excluding the active one)
        if hasattr(self, "_db_recent"):
            for s in self._db_recent:
                if app.db_session_id is not None and s.id == app.db_session_id:
                    continue  # skip — it's shown as the live row
                dist = s.total_distance_m / (1609.344 if use_miles else 1000)
                avg = s.avg_speed_kmh * (0.621371 if use_miles else 1)
                mx = s.max_speed_kmh * (0.621371 if use_miles else 1)
                table.add_row(
                    s.date_str,
                    s.elapsed_fmt,
                    f"{dist:.2f}",
                    f"{avg:.1f}",
                    f"{mx:.1f}",
                    str(s.calories),
                )

    def _update_live_row(self) -> None:
        """Update just the live session row in the table."""
        app = self._get_app()
        session = app.session
        table = self.query_one("#recent-table", DataTable)
        use_miles = app.config.use_miles

        if session.sample_count == 0:
            return

        dist = session.total_distance_m / (1609.344 if use_miles else 1000)
        avg = session.avg_speed_kmh * (0.621371 if use_miles else 1)
        mx = session.max_speed_kmh * (0.621371 if use_miles else 1)

        try:
            row_key = table.get_row("live")  # noqa — just checking existence
            # Update each cell in the live row
            cols = list(table.columns.keys())
            table.update_cell("live", cols[1], f"[bold green]{session.elapsed_time_fmt}[/]")
            table.update_cell("live", cols[2], f"[bold green]{dist:.2f}[/]")
            table.update_cell("live", cols[3], f"[bold green]{avg:.1f}[/]")
            table.update_cell("live", cols[4], f"[bold green]{mx:.1f}[/]")
            table.update_cell("live", cols[5], f"[bold green]{session.total_energy_kcal}[/]")
        except Exception:
            # Row doesn't exist yet — rebuild will add it
            self._rebuild_table()

    def _refresh_live(self) -> None:
        """Update current-session stats live."""
        app = self._get_app()

        # Reload DB stats if a session was finalized while we're viewing
        if app._db_stats_dirty:
            app._db_stats_dirty = False
            self.run_worker(self._load_db_stats())

        data = app._latest_data
        session = app.session
        use_miles = app.config.use_miles
        km_to_unit = 0.621371 if use_miles else 1.0

        # Current session widgets
        speed = data.speed_mph if use_miles else data.instantaneous_speed_kmh
        self.query_one("#cs-speed", BigStat).value = f"{speed:.1f}"

        if data.total_distance_m is not None:
            d = data.distance_miles if use_miles else data.total_distance_m / 1000
            self.query_one("#cs-distance", BigStat).value = f"{d:.2f}"

        self.query_one("#cs-time", BigStat).value = data.elapsed_time_fmt

        if data.total_energy_kcal is not None:
            self.query_one("#cs-calories", BigStat).value = str(data.total_energy_kcal)

        # Update live row in the table
        self._update_live_row()

        # Merge current session into lifetime + today stats
        if hasattr(self, "_db_lifetime"):
            lt = self._db_lifetime
            cur_dist = session.total_distance_m
            cur_elapsed = session.last_elapsed_s or int(session.duration_s)
            cur_cals = session.total_energy_kcal

            total_dist = (lt.total_distance_m + cur_dist) / (1609.344 if use_miles else 1000)
            self.query_one("#lt-sessions", BigStat).value = str(lt.total_sessions + 1)
            self.query_one("#lt-distance", BigStat).value = f"{total_dist:.2f}"

            total_s = lt.total_elapsed_s + cur_elapsed
            m, s = divmod(total_s, 60)
            h, m = divmod(m, 60)
            self.query_one("#lt-time", BigStat).value = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
            self.query_one("#lt-calories", BigStat).value = str(lt.total_calories + cur_cals)

            # Bests (include current)
            best_speed = max(lt.max_speed_kmh, session.max_speed_kmh) * km_to_unit
            self.query_one("#pb-speed", BigStat).value = f"{best_speed:.1f}"
            best_dur = max(lt.longest_session_s, cur_elapsed)
            bm, bs = divmod(best_dur, 60)
            bh, bm = divmod(bm, 60)
            self.query_one("#pb-duration", BigStat).value = f"{bh}:{bm:02d}:{bs:02d}" if bh else f"{bm}:{bs:02d}"
            best_dist = max(lt.longest_distance_m, cur_dist) / (1609.344 if use_miles else 1000)
            self.query_one("#pb-distance", BigStat).value = f"{best_dist:.2f}"

        if hasattr(self, "_db_today"):
            td = self._db_today
            cur_dist = session.total_distance_m
            cur_elapsed = session.last_elapsed_s or int(session.duration_s)
            cur_cals = session.total_energy_kcal

            self.query_one("#td-sessions", BigStat).value = str(td.sessions + 1)
            td_dist = (td.distance_m + cur_dist) / (1609.344 if use_miles else 1000)
            self.query_one("#td-distance", BigStat).value = f"{td_dist:.2f}"

            td_s = td.elapsed_s + cur_elapsed
            m, s = divmod(td_s, 60)
            h, m = divmod(m, 60)
            self.query_one("#td-time", BigStat).value = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
            self.query_one("#td-calories", BigStat).value = str(td.calories + cur_cals)

    async def _load_db_stats(self) -> None:
        """Load persisted stats from DB (excluding current session to avoid double-counting)."""
        app = self._get_app()
        repo = app.repo
        if repo is None:
            return

        exclude_id = app.db_session_id
        self._db_lifetime, self._db_today, self._db_recent = await asyncio.gather(
            repo.get_lifetime_stats(exclude_session_id=exclude_id),
            repo.get_today_stats(exclude_session_id=exclude_id),
            repo.get_recent_sessions(limit=20),
        )

        self._rebuild_table()


# ---------------------------------------------------------------------------
# Main Dashboard
# ---------------------------------------------------------------------------

class TreadmillDashboard(App):
    """Live treadmill dashboard TUI."""

    CSS = """
    Screen {
        layout: vertical;
    }

    #top-bar {
        height: 1;
        dock: top;
        background: $surface;
        padding: 0 1;
    }

    #speed-container {
        height: 5;
        padding: 1 2;
        background: $surface-darken-1;
        margin: 0 1;
    }

    #stats-row {
        height: 5;
        padding: 1 0;
        margin: 0 1;
    }

    #stats-row > Static {
        width: 1fr;
        padding: 0 2;
    }

    #session-row {
        height: 5;
        padding: 1 0;
        margin: 0 1;
        background: $surface-darken-1;
    }

    #session-row > Static {
        width: 1fr;
        padding: 0 2;
    }

    BigStat {
        width: 1fr;
        padding: 0 2;
    }

    #meeting-bar {
        height: 1;
        padding: 0 1;
        background: $surface;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("u", "toggle_units", "Toggle km/mph"),
        ("s", "show_stats", "Stats"),
        ("c", "copy_current", "📋 Session"),
        ("t", "copy_today", "📋 Today"),
        ("l", "copy_lifetime", "📋 Lifetime"),
        ("m", "copy_meeting", "📋 Meeting"),
    ]

    def __init__(self, config: Config | None = None, repo=None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.config = config or Config()
        self.session = SessionStats()
        self._latest_data = TreadmillData()
        self._connection_status = "Disconnected"
        self.repo = repo  # Optional[Repository]
        self.db_session_id: Optional[int] = None
        self._db_stats_dirty = False  # set True when a session is finalized mid-app
        self._meeting: Optional[MeetingStats] = None
        self._last_meeting: Optional[MeetingStats] = None  # retained after meeting ends

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield ConnectionStatus(id="top-bar")
        yield SpeedGauge(id="speed-container")
        with Horizontal(id="stats-row"):
            yield BigStat(label="📏 DISTANCE", unit=self.config.distance_unit, id="distance")
            yield BigStat(label="⏱️ TIME", id="time")
            yield BigStat(label="🔥 CALORIES", unit="kcal", id="calories")
            yield BigStat(label="⛰️ INCLINE", unit="%", id="incline")
        with Horizontal(id="session-row"):
            yield BigStat(label="⚡ AVG SPEED", unit=self.config.speed_unit, id="avg-speed")
            yield BigStat(label="🚀 MAX SPEED", unit=self.config.speed_unit, id="max-speed")
        yield Static("", id="meeting-bar")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "🏃 Treadmill Dashboard"
        self.sub_title = "Waiting for data..."
        self.set_interval(0.5, self._refresh_display)
        self.set_interval(5.0, self._poll_meeting)

    def update_data(self, data: TreadmillData) -> None:
        """Called from the BLE callback with new treadmill data."""
        self._latest_data = data
        self.session.update(data)

    def update_connection_status(self, status: str) -> None:
        """Called from the BLE connection manager."""
        self._connection_status = status

    def _refresh_display(self) -> None:
        """Periodically refresh all widgets from latest data."""
        data = self._latest_data
        session = self.session
        use_miles = self.config.use_miles

        # Connection status
        status_widget = self.query_one("#top-bar", ConnectionStatus)
        status_widget.status = self._connection_status

        # Speed (instantaneous — always from live data)
        speed_widget = self.query_one("#speed-container", SpeedGauge)
        speed_widget.speed = data.speed_mph if use_miles else data.instantaneous_speed_kmh
        speed_widget.unit = self.config.speed_unit

        # Distance (treadmill cumulative)
        dist = self.query_one("#distance", BigStat)
        d = session.total_distance_m / (1609.344 if use_miles else 1000)
        dist.value = f"{d:.2f}"
        dist.unit = self.config.distance_unit

        # Time (treadmill session elapsed)
        time_w = self.query_one("#time", BigStat)
        time_w.value = session.elapsed_time_fmt

        # Calories (treadmill cumulative)
        cal = self.query_one("#calories", BigStat)
        cal.value = str(session.total_energy_kcal)

        # Incline (instantaneous — always from live data)
        incl = self.query_one("#incline", BigStat)
        if data.inclination_pct is not None:
            incl.value = f"{data.inclination_pct:.1f}"

        # Session stats
        avg_w = self.query_one("#avg-speed", BigStat)
        avg_spd = self.session.avg_speed_kmh * (0.621371 if use_miles else 1)
        avg_w.value = f"{avg_spd:.1f}"
        avg_w.unit = self.config.speed_unit

        max_w = self.query_one("#max-speed", BigStat)
        max_spd = self.session.max_speed_kmh * (0.621371 if use_miles else 1)
        max_w.value = f"{max_spd:.1f}"
        max_w.unit = self.config.speed_unit


        if self.session.sample_count > 0:
            self.sub_title = "Receiving data"

        # Update meeting bar
        self._update_meeting_bar()

    def _poll_meeting(self) -> None:
        """Check for active Teams meeting every ~5 seconds."""
        status = get_active_meeting()

        if status.in_meeting and status.meeting_name:
            if self._meeting is None or self._meeting.meeting_name != status.meeting_name:
                # Save outgoing meeting if switching between meetings
                if self._meeting is not None:
                    self._meeting.update(self.session)
                    self._save_meeting(self._meeting)
                    self._last_meeting = self._meeting

                # New meeting started (or switched meetings)
                session = self.session
                self._meeting = MeetingStats(
                    meeting_name=status.meeting_name,
                    start_distance_m=session.total_distance_m,
                    start_elapsed_s=session.last_elapsed_s or int(session.duration_s),
                    start_calories=session.total_energy_kcal,
                )
                self.notify(
                    f"Tracking stats for: {status.meeting_name}",
                    title="📞 Meeting detected",
                )
            else:
                # Same meeting — update stats
                self._meeting.update(self.session)
        else:
            if self._meeting is not None:
                self._meeting.update(self.session)
                self._save_meeting(self._meeting)
                use_miles = self.config.use_miles
                dist = self._meeting.distance_m / (1609.344 if use_miles else 1000)
                du = self.config.distance_unit
                self.notify(
                    f"{self._meeting.meeting_name} — "
                    f"{self._meeting.elapsed_fmt}, "
                    f"{dist:.2f} {du}",
                    title="📞 Meeting ended",
                )
                self._last_meeting = self._meeting
                self._meeting = None

    def _save_meeting(self, meeting: MeetingStats) -> None:
        """Persist a completed meeting to the database."""
        if self.repo is None:
            return
        try:
            asyncio.ensure_future(
                self.repo.save_meeting(self.db_session_id, meeting)
            )
        except Exception:
            pass  # best-effort; don't crash the UI

    def _update_meeting_bar(self) -> None:
        """Refresh the meeting indicator bar."""
        bar = self.query_one("#meeting-bar", Static)
        if self._meeting is None:
            bar.update("")
            return

        self._meeting.update(self.session)
        use_miles = self.config.use_miles
        dist = self._meeting.distance_m / (1609.344 if use_miles else 1000)
        du = self.config.distance_unit
        bar.update(
            f"[bold magenta]📞 {self._meeting.meeting_name}[/]  "
            f"⏱️ {self._meeting.elapsed_fmt}  "
            f"📏 {dist:.2f} {du}  "
            f"🔥 {self._meeting.calories} kcal"
        )

    def action_toggle_units(self) -> None:
        self.config.use_miles = not self.config.use_miles
        self.query_one("#distance", BigStat).unit = self.config.distance_unit
        self.query_one("#avg-speed", BigStat).unit = self.config.speed_unit
        self.query_one("#max-speed", BigStat).unit = self.config.speed_unit

    def action_show_stats(self) -> None:
        """Open the stats screen."""
        self.push_screen(StatsScreen())

    def action_copy_current(self) -> None:
        """Copy current session stats as HTML to clipboard."""
        html, text = current_session_html(self.session, self.config)
        try:
            copy_html_to_clipboard(html, text)
            self.notify("Current session copied!", title="📋")
        except Exception as e:
            self.notify(f"Clipboard error: {e}", severity="error")

    async def action_copy_today(self) -> None:
        """Copy today's stats (DB + live) as HTML to clipboard."""
        if self.repo is None:
            self.notify("No database configured", severity="warning")
            return
        today = await self.repo.get_today_stats(
            exclude_session_id=self.db_session_id
        )
        html, text = today_stats_html(today, self.session, self.config)
        try:
            copy_html_to_clipboard(html, text)
            self.notify("Today's stats copied!", title="📋")
        except Exception as e:
            self.notify(f"Clipboard error: {e}", severity="error")

    async def action_copy_lifetime(self) -> None:
        """Copy lifetime stats (DB + live) as HTML to clipboard."""
        if self.repo is None:
            self.notify("No database configured", severity="warning")
            return
        lifetime = await self.repo.get_lifetime_stats(
            exclude_session_id=self.db_session_id
        )
        html, text = lifetime_stats_html(lifetime, self.session, self.config)
        try:
            copy_html_to_clipboard(html, text)
            self.notify("Lifetime stats copied!", title="📋")
        except Exception as e:
            self.notify(f"Clipboard error: {e}", severity="error")

    def action_copy_meeting(self) -> None:
        """Copy current or last meeting stats as HTML to clipboard."""
        meeting = self._meeting or self._last_meeting
        if meeting is None:
            self.notify("No meeting stats available", severity="warning")
            return
        if self._meeting is not None:
            meeting.update(self.session)
        html, text = meeting_stats_html(meeting, self.config)
        try:
            copy_html_to_clipboard(html, text)
            label = "Meeting stats copied!" if self._meeting else f"Last meeting ({meeting.meeting_name}) copied!"
            self.notify(label, title="📋")
        except Exception as e:
            self.notify(f"Clipboard error: {e}", severity="error")

    def on_unmount(self) -> None:
        """Print session summary to terminal after the app exits."""
        pass  # handled in __main__
