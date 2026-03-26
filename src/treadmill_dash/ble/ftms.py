"""FTMS (Fitness Machine Service) protocol parser for treadmill data."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntFlag
from typing import Optional

from treadmill_dash.models import TreadmillData

# FTMS UUIDs
FTMS_SERVICE_UUID = "00001826-0000-1000-8000-00805f9b34fb"
TREADMILL_DATA_UUID = "00002acd-0000-1000-8000-00805f9b34fb"
FITNESS_MACHINE_FEATURE_UUID = "00002acc-0000-1000-8000-00805f9b34fb"
SUPPORTED_SPEED_RANGE_UUID = "00002ad4-0000-1000-8000-00805f9b34fb"
TRAINING_STATUS_UUID = "00002ad3-0000-1000-8000-00805f9b34fb"
FITNESS_MACHINE_STATUS_UUID = "00002ada-0000-1000-8000-00805f9b34fb"


class TreadmillDataFlags(IntFlag):
    """Bit flags in the Treadmill Data characteristic (bytes 0-1).

    NOTE: In FTMS, bit=0 means the field IS present for the speed field (bit 0),
    but bit=1 means the field IS present for all other optional fields.
    We handle this quirk in the parser.
    """
    MORE_DATA = 1 << 0           # 0 = speed present, 1 = speed NOT present (inverted!)
    AVERAGE_SPEED = 1 << 1
    TOTAL_DISTANCE = 1 << 2
    INCLINATION = 1 << 3
    ELEVATION_GAIN = 1 << 4
    INSTANTANEOUS_PACE = 1 << 5
    AVERAGE_PACE = 1 << 6
    EXPENDED_ENERGY = 1 << 7
    HEART_RATE = 1 << 8
    METABOLIC_EQUIVALENT = 1 << 9
    ELAPSED_TIME = 1 << 10
    REMAINING_TIME = 1 << 11


def parse_treadmill_data(data: bytes | bytearray) -> TreadmillData:
    """Parse a raw FTMS Treadmill Data notification into a TreadmillData object.

    The Treadmill Data characteristic has a variable-length format:
      - Bytes 0-1: Flags (uint16, little-endian)
      - Remaining bytes: Fields present according to flags, in order

    Reference: Bluetooth GATT Specification Supplement, Section 3.216
    """
    if len(data) < 2:
        return TreadmillData()

    flags = struct.unpack_from("<H", data, 0)[0]
    offset = 2
    result = TreadmillData()

    # Bit 0 (MORE_DATA): 0 = instantaneous speed present, 1 = not present
    if not (flags & TreadmillDataFlags.MORE_DATA):
        if offset + 2 <= len(data):
            raw_speed = struct.unpack_from("<H", data, offset)[0]
            result.instantaneous_speed_kmh = raw_speed / 100.0  # resolution: 0.01 km/h
            offset += 2

    # Bit 1: Average Speed (uint16, 0.01 km/h)
    if flags & TreadmillDataFlags.AVERAGE_SPEED:
        if offset + 2 <= len(data):
            raw = struct.unpack_from("<H", data, offset)[0]
            result.average_speed_kmh = raw / 100.0
            offset += 2

    # Bit 2: Total Distance (uint24, 1 meter)
    if flags & TreadmillDataFlags.TOTAL_DISTANCE:
        if offset + 3 <= len(data):
            b = data[offset:offset + 3]
            result.total_distance_m = float(b[0] | (b[1] << 8) | (b[2] << 16))
            offset += 3

    # Bit 3: Inclination (sint16, 0.1%) + Ramp Angle (sint16, 0.1 deg)
    if flags & TreadmillDataFlags.INCLINATION:
        if offset + 4 <= len(data):
            raw_incl, raw_ramp = struct.unpack_from("<hh", data, offset)
            result.inclination_pct = raw_incl / 10.0
            result.ramp_angle_deg = raw_ramp / 10.0
            offset += 4

    # Bit 4: Elevation Gain (positive uint16 + negative uint16, 0.1 m)
    if flags & TreadmillDataFlags.ELEVATION_GAIN:
        if offset + 4 <= len(data):
            pos, neg = struct.unpack_from("<HH", data, offset)
            result.positive_elevation_gain_m = pos / 10.0
            result.negative_elevation_gain_m = neg / 10.0
            offset += 4

    # Bit 5: Instantaneous Pace (uint8, 0.1 km/min — but really min/km for display)
    if flags & TreadmillDataFlags.INSTANTANEOUS_PACE:
        if offset + 1 <= len(data):
            raw = data[offset]
            result.instantaneous_pace_min_per_km = raw / 10.0
            offset += 1

    # Bit 6: Average Pace (uint8, 0.1 min/km)
    if flags & TreadmillDataFlags.AVERAGE_PACE:
        if offset + 1 <= len(data):
            raw = data[offset]
            result.average_pace_min_per_km = raw / 10.0
            offset += 1

    # Bit 7: Expended Energy — Total (uint16 kcal) + Per Hour (uint16) + Per Minute (uint8)
    if flags & TreadmillDataFlags.EXPENDED_ENERGY:
        if offset + 5 <= len(data):
            total, per_hr = struct.unpack_from("<HH", data, offset)
            per_min = data[offset + 4]
            # 0xFFFF means "not available"
            result.total_energy_kcal = None if total == 0xFFFF else total
            result.energy_per_hour_kcal = None if per_hr == 0xFFFF else per_hr
            result.energy_per_minute_kcal = None if per_min == 0xFF else per_min
            offset += 5

    # Bit 8: Heart Rate (uint8 bpm)
    if flags & TreadmillDataFlags.HEART_RATE:
        if offset + 1 <= len(data):
            hr = data[offset]
            result.heart_rate_bpm = None if hr == 0 else hr
            offset += 1

    # Bit 9: Metabolic Equivalent (uint8, 0.1 resolution)
    if flags & TreadmillDataFlags.METABOLIC_EQUIVALENT:
        if offset + 1 <= len(data):
            raw = data[offset]
            result.metabolic_equivalent = raw / 10.0
            offset += 1

    # Bit 10: Elapsed Time (uint16, seconds)
    if flags & TreadmillDataFlags.ELAPSED_TIME:
        if offset + 2 <= len(data):
            result.elapsed_time_s = struct.unpack_from("<H", data, offset)[0]
            offset += 2

    # Bit 11: Remaining Time (uint16, seconds)
    if flags & TreadmillDataFlags.REMAINING_TIME:
        if offset + 2 <= len(data):
            result.remaining_time_s = struct.unpack_from("<H", data, offset)[0]
            offset += 2

    return result


@dataclass
class SpeedRange:
    min_kmh: float
    max_kmh: float
    increment_kmh: float


def parse_speed_range(data: bytes | bytearray) -> Optional[SpeedRange]:
    """Parse Supported Speed Range characteristic (0x2AD4).

    Format: min (uint16, 0.01 km/h) + max (uint16) + increment (uint16)
    """
    if len(data) < 6:
        return None
    raw_min, raw_max, raw_inc = struct.unpack_from("<HHH", data, 0)
    return SpeedRange(
        min_kmh=raw_min / 100.0,
        max_kmh=raw_max / 100.0,
        increment_kmh=raw_inc / 100.0,
    )
