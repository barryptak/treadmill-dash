"""Entry point for treadmill-dash."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import threading
from pathlib import Path
from typing import Optional

from treadmill_dash.ble.connection import TreadmillConnection
from treadmill_dash.config import Config
from treadmill_dash.db.repository import Repository, DEFAULT_DB_PATH
from treadmill_dash.models import TreadmillData
from treadmill_dash.ui.dashboard import TreadmillDashboard

# Sample recording: save one sample every N BLE notifications to avoid
# excessive DB writes (treadmill typically sends ~1–2 notifications/sec)
SAMPLE_EVERY_N = 5


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="treadmill-dash",
        description="Live treadmill dashboard via Bluetooth FTMS",
    )
    parser.add_argument(
        "--address", "-a",
        help="BLE address of the treadmill (e.g., AA:BB:CC:DD:EE:FF). "
             "If omitted, auto-discovers the first FTMS device.",
    )
    parser.add_argument(
        "--miles", action="store_true",
        help="Display speed in mph and distance in miles (default: km/h and km)",
    )
    parser.add_argument(
        "--db", type=str, default=None,
        help=f"Path to SQLite database (default: {DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--no-db", action="store_true",
        help="Disable session persistence",
    )
    parser.add_argument(
        "--export", type=int, metavar="SESSION_ID",
        help="Export a past session to CSV and exit",
    )
    args = parser.parse_args()

    # Handle CSV export mode
    if args.export is not None:
        asyncio.run(_export_session(args.export, args.db))
        return

    config = Config(device_address=args.address, use_miles=args.miles)
    db_path = Path(args.db) if args.db else DEFAULT_DB_PATH
    use_db = not args.no_db

    # Set up persistence
    repo: Optional[Repository] = None
    if use_db:
        repo = Repository(db_path)
        asyncio.run(repo.open())

    app = TreadmillDashboard(config=config, repo=repo)

    # Dedicated event loop for DB operations from the BLE thread
    _db_loop = asyncio.new_event_loop()

    def run_db_loop() -> None:
        asyncio.set_event_loop(_db_loop)
        _db_loop.run_forever()

    db_thread = threading.Thread(target=run_db_loop, daemon=True, name="db-thread")
    db_thread.start()

    # Session state — resolved on first data sample
    db_session_id: Optional[int] = None
    session_resolved = False
    sample_counter = 0

    def _resolve_session(data: TreadmillData) -> None:
        """Decide whether to resume an existing DB session or create a new one.

        Called once on the first data sample.  Uses the treadmill's current
        distance and elapsed time to detect whether this is the same treadmill
        session as the most recent DB entry.
        """
        nonlocal db_session_id, session_resolved
        session_resolved = True

        if repo is None:
            return

        treadmill_dist = data.total_distance_m or 0.0
        treadmill_elapsed = data.elapsed_time_s or 0

        # Run the resume check synchronously on the DB event loop
        future = asyncio.run_coroutine_threadsafe(
            repo.try_resume_session(treadmill_dist, treadmill_elapsed),
            _db_loop,
        )
        resumed_id = future.result(timeout=5)

        if resumed_id is not None:
            db_session_id = resumed_id
            app.db_session_id = resumed_id
            # Restore max speed from previous connection(s) within this session
            max_spd_future = asyncio.run_coroutine_threadsafe(
                repo.get_session_max_speed(resumed_id),
                _db_loop,
            )
            prev_max = max_spd_future.result(timeout=5)
            app.session.max_speed_kmh = max(app.session.max_speed_kmh, prev_max)
            logging.getLogger(__name__).info(
                f"Resuming treadmill session #{resumed_id}"
            )
        else:
            future = asyncio.run_coroutine_threadsafe(
                repo.start_session(),
                _db_loop,
            )
            db_session_id = future.result(timeout=5)
            app.db_session_id = db_session_id
            logging.getLogger(__name__).info(
                f"New treadmill session #{db_session_id}"
            )

    def on_data(data: TreadmillData) -> None:
        """BLE callback — runs in the BLE thread."""
        nonlocal sample_counter
        try:
            app.call_from_thread(app.update_data, data)
        except RuntimeError:
            return  # app stopped

        # On first sample, decide whether to resume or create a session
        if not session_resolved:
            try:
                _resolve_session(data)
            except Exception as e:
                logging.getLogger(__name__).error(f"Session resolve error: {e}")

        # Persist samples periodically
        if repo and db_session_id is not None:
            sample_counter += 1
            if sample_counter % SAMPLE_EVERY_N == 0:
                try:
                    asyncio.run_coroutine_threadsafe(
                        repo.add_sample(db_session_id, data),
                        _db_loop,
                    )
                except Exception:
                    pass

    def on_status(status: str) -> None:
        """BLE status callback — runs in the BLE thread."""
        try:
            app.call_from_thread(app.update_connection_status, status)
        except RuntimeError:
            pass  # app stopped

    connection = TreadmillConnection(
        address=config.device_address,
        on_data=on_data,
        on_status=on_status,
    )

    async def ble_main() -> None:
        """BLE event loop running in background thread."""
        await connection.connect()
        await connection.reconnect_loop()

    def run_ble() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(ble_main())
        except Exception as e:
            logging.getLogger(__name__).error(f"BLE thread error: {e}", exc_info=True)
            on_status(f"BLE error: {e}")
        finally:
            loop.close()

    ble_thread = threading.Thread(target=run_ble, daemon=True, name="ble-thread")
    ble_thread.start()

    # Run the Textual app (blocks until user quits)
    app.run()

    # After exit, disconnect BLE and persist session
    asyncio.run(connection.disconnect())

    if repo and db_session_id is not None and app.session.sample_count > 0:
        asyncio.run(
            repo.end_session(
                session_id=db_session_id,
                total_distance_m=app.session.total_distance_m,
                avg_speed_kmh=app.session.avg_speed_kmh,
                max_speed_kmh=app.session.max_speed_kmh,
                calories=app.session.total_energy_kcal,
                elapsed_s=app.session.last_elapsed_s,
                sample_count=app.session.sample_count,
            )
        )

    if repo:
        asyncio.run(repo.close())

    # Stop the DB event loop
    _db_loop.call_soon_threadsafe(_db_loop.stop)

    if app.session.sample_count > 0:
        print()
        print(app.session.summary())
    else:
        print("\nNo data received during session.")


async def _export_session(session_id: int, db_path: str | None) -> None:
    """Export a session's samples to CSV."""
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    repo = Repository(path)
    await repo.open()
    try:
        csv_text = await repo.export_session_csv(session_id)
        print(csv_text)
    finally:
        await repo.close()


if __name__ == "__main__":
    main()
