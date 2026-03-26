"""BLE connection management for FTMS treadmills."""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice

from treadmill_dash.ble.ftms import (
    FTMS_SERVICE_UUID,
    TREADMILL_DATA_UUID,
    FITNESS_MACHINE_FEATURE_UUID,
    SUPPORTED_SPEED_RANGE_UUID,
    parse_treadmill_data,
    parse_speed_range,
    SpeedRange,
)
from treadmill_dash.models import TreadmillData

log = logging.getLogger(__name__)

# Reconnection settings
RECONNECT_DELAY_S = 3.0
MAX_RECONNECT_ATTEMPTS = 10


class TreadmillConnection:
    """Manages BLE connection to an FTMS treadmill with auto-reconnect."""

    def __init__(
        self,
        address: Optional[str] = None,
        on_data: Optional[Callable[[TreadmillData], None]] = None,
        on_status: Optional[Callable[[str], None]] = None,
    ):
        self._address = address
        self._client: Optional[BleakClient] = None
        self._on_data = on_data
        self._on_status = on_status
        self._connected = False
        self._running = False
        self._speed_range: Optional[SpeedRange] = None

    @property
    def connected(self) -> bool:
        return self._connected and self._client is not None and self._client.is_connected

    @property
    def speed_range(self) -> Optional[SpeedRange]:
        return self._speed_range

    def _status(self, msg: str) -> None:
        log.info(msg)
        if self._on_status:
            try:
                self._on_status(msg)
            except RuntimeError:
                pass  # app may have stopped

    async def _find_device(self) -> Optional[BLEDevice]:
        """Find the treadmill by address or auto-discover the first FTMS device."""
        if self._address:
            self._status(f"Connecting to {self._address}...")
            device = await BleakScanner.find_device_by_address(self._address, timeout=10.0)
            if device:
                return device
            self._status(f"Device {self._address} not found")
            return None

        self._status("Scanning for FTMS treadmills...")
        devices = await BleakScanner.discover(timeout=10.0, return_adv=True)
        for _addr, (d, adv) in devices.items():
            if FTMS_SERVICE_UUID in (adv.service_uuids or []):
                self._status(f"Found {d.name or 'Unknown'} [{d.address}]")
                return d
        self._status("No FTMS treadmill found during scan")
        return None

    def _handle_notification(self, _sender: int, data: bytearray) -> None:
        """Handle incoming Treadmill Data notifications."""
        parsed = parse_treadmill_data(data)
        if self._on_data:
            try:
                self._on_data(parsed)
            except RuntimeError:
                pass  # app may have stopped

    async def connect(self) -> bool:
        """Establish connection and start receiving data."""
        device = await self._find_device()
        if not device:
            return False

        try:
            self._client = BleakClient(device, disconnected_callback=self._on_disconnect)
            await self._client.connect()
            self._connected = True
            self._status(f"Connected to {device.name or device.address}")

            # Read supported speed range
            try:
                speed_data = await self._client.read_gatt_char(SUPPORTED_SPEED_RANGE_UUID)
                self._speed_range = parse_speed_range(speed_data)
                if self._speed_range:
                    log.info(
                        f"Speed range: {self._speed_range.min_kmh}-{self._speed_range.max_kmh} km/h"
                    )
            except Exception as e:
                log.debug(f"Could not read speed range: {e}")

            # Subscribe to treadmill data notifications
            await self._client.start_notify(TREADMILL_DATA_UUID, self._handle_notification)
            self._status("Receiving treadmill data...")
            return True

        except Exception as e:
            self._status(f"Connection failed: {e}")
            self._connected = False
            return False

    def _on_disconnect(self, _client: BleakClient) -> None:
        self._connected = False
        self._status("Disconnected from treadmill")

    async def reconnect_loop(self) -> None:
        """Continuously attempt to reconnect when disconnected."""
        self._running = True
        attempts = 0

        while self._running:
            if not self.connected:
                attempts += 1
                if attempts > MAX_RECONNECT_ATTEMPTS:
                    self._status(f"Max reconnect attempts ({MAX_RECONNECT_ATTEMPTS}) reached")
                    break

                self._status(f"Reconnecting (attempt {attempts}/{MAX_RECONNECT_ATTEMPTS})...")
                if await self.connect():
                    attempts = 0  # reset on successful connection

                if not self.connected:
                    await asyncio.sleep(RECONNECT_DELAY_S)
            else:
                attempts = 0
                await asyncio.sleep(1.0)

    async def disconnect(self) -> None:
        """Cleanly disconnect."""
        self._running = False
        if self._client and self._client.is_connected:
            try:
                await self._client.stop_notify(TREADMILL_DATA_UUID)
            except Exception:
                pass
            await self._client.disconnect()
        self._connected = False
        self._status("Disconnected")
