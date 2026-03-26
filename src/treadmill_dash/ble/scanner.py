"""Scan for nearby FTMS-compatible treadmills."""

import asyncio

from bleak import BleakScanner
from bleak.backends.device import BLEDevice

FTMS_SERVICE_UUID = "00001826-0000-1000-8000-00805f9b34fb"


async def scan_for_treadmills(timeout: float = 10.0) -> list[BLEDevice]:
    """Scan for BLE devices advertising the FTMS service.

    Uses discover() rather than a detection callback to avoid a Windows BLE
    race where the first advertisement for a device may arrive before its
    service UUIDs have been resolved.
    """
    print(f"Scanning for FTMS treadmills ({timeout}s)...\n")

    devices = await BleakScanner.discover(timeout=timeout, return_adv=True)

    found: list[BLEDevice] = []
    for _addr, (device, adv) in devices.items():
        if FTMS_SERVICE_UUID in (adv.service_uuids or []):
            found.append(device)
            print(f"  ✓ {device.name or 'Unknown'} [{device.address}]  RSSI={adv.rssi}")

    if not found:
        print("  No FTMS treadmills found.")
        print("  Make sure your treadmill is on and in pairing/discoverable mode.")
    else:
        print(f"\nFound {len(found)} device(s). Use the address with:")
        print("  treadmill-dash --address <ADDRESS>")

    return found


def main() -> None:
    asyncio.run(scan_for_treadmills())


if __name__ == "__main__":
    main()
