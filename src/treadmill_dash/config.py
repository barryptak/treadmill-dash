"""Application configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class Config:
    """Runtime configuration."""

    # BLE device address (None = auto-discover first FTMS device)
    device_address: Optional[str] = None

    # Display units
    use_miles: bool = False  # True = mph/miles, False = km/h/km

    @property
    def speed_unit(self) -> str:
        return "mph" if self.use_miles else "km/h"

    @property
    def distance_unit(self) -> str:
        return "mi" if self.use_miles else "km"
