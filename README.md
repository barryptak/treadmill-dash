# 🏃 Treadmill Dashboard

Live treadmill stats via Bluetooth Low Energy (FTMS profile) with a terminal UI.

## Quick Start

```bash
# Install
pip install -e .

# Scan for treadmills
treadmill-scan

# Launch dashboard (pass the BLE address from scan)
treadmill-dash --address XX:XX:XX:XX:XX:XX

# Or auto-discover the first FTMS device
treadmill-dash

# Use miles instead of km
treadmill-dash --miles
```

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `u` | Toggle km/h ↔ mph |
| `s` | Stats & history screen |
| `q` | Quit (shows session summary) |

## Features

- 📡 Connects to any FTMS-compatible treadmill via BLE
- 🖥️ Real-time terminal dashboard: speed gauge, distance, time, calories, inclination
- 🔄 Auto-reconnect on connection drops
- 💾 SQLite session persistence (`~/.treadmill-dash/data.db`)
- 🧠 Smart session continuity — detects same treadmill session across app restarts
- 📊 Stats screen: lifetime totals, today's totals, personal bests, recent sessions
- 📤 CSV export: `treadmill-dash --export <SESSION_ID>`

## CLI Options

```
--address, -a    BLE address (auto-discovers if omitted)
--miles          Display in mph/miles
--db PATH        Custom database path
--no-db          Disable persistence
--export ID      Export a session to CSV
```
