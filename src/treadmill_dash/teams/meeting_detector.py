"""Detect active Teams meetings via Win32 window titles + microphone status.

Combines two local signals (no Graph API needed):

1. **Microphone registry** — checks if Teams has an active audio device by
   reading ``LastUsedTimeStop`` from the Windows CapabilityAccessManager.
   A value of 0 means the mic is currently open (in a call/meeting).

2. **Window enumeration** — finds visible ``ms-teams`` windows whose titles
   match ``{name} | Microsoft Teams`` and filters out chat windows
   (``Chat | …`` prefix).

Only reports a meeting when *both* signals are true: mic is active AND a
meeting-titled window exists.  This correctly ignores pop-out chat windows
that share titles with scheduled meetings.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import re
import winreg
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Registry-based microphone check
# ---------------------------------------------------------------------------

_MIC_REG_PATH = (
    r"Software\Microsoft\Windows\CurrentVersion"
    r"\CapabilityAccessManager\ConsentStore\microphone"
)
_TEAMS_KEY_PATTERN = re.compile(r"MSTeams", re.IGNORECASE)


def _is_teams_mic_active() -> bool:
    """Return True if Teams currently has the microphone open."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _MIC_REG_PATH) as parent:
            i = 0
            while True:
                try:
                    subkey_name = winreg.EnumKey(parent, i)
                    i += 1
                except OSError:
                    break
                if not _TEAMS_KEY_PATTERN.search(subkey_name):
                    continue
                try:
                    with winreg.OpenKey(parent, subkey_name) as sk:
                        stop, _ = winreg.QueryValueEx(sk, "LastUsedTimeStop")
                        if stop == 0:
                            return True
                except OSError:
                    continue
    except OSError:
        log.debug("Microphone registry key not found")
    return False


# ---------------------------------------------------------------------------
# Window enumeration
# ---------------------------------------------------------------------------

# ctypes function signatures (64-bit safe)
_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32

_EnumWindowsProc = ctypes.WINFUNCTYPE(
    ctypes.wintypes.BOOL, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM
)

_user32.EnumWindows.argtypes = [_EnumWindowsProc, ctypes.wintypes.LPARAM]
_user32.EnumWindows.restype = ctypes.wintypes.BOOL

_user32.GetWindowTextW.argtypes = [
    ctypes.wintypes.HWND, ctypes.wintypes.LPWSTR, ctypes.c_int,
]
_user32.GetWindowTextW.restype = ctypes.c_int

_user32.GetWindowTextLengthW.argtypes = [ctypes.wintypes.HWND]
_user32.GetWindowTextLengthW.restype = ctypes.c_int

_user32.IsWindowVisible.argtypes = [ctypes.wintypes.HWND]
_user32.IsWindowVisible.restype = ctypes.wintypes.BOOL

_user32.GetWindowThreadProcessId.argtypes = [
    ctypes.wintypes.HWND, ctypes.POINTER(ctypes.wintypes.DWORD),
]
_user32.GetWindowThreadProcessId.restype = ctypes.wintypes.DWORD

_TEAMS_SUFFIX = " | Microsoft Teams"
_CHAT_PREFIX = "Chat | "

# Patterns that indicate a chat/channel window, not a meeting.
# Only filter the unambiguous "Chat | …" prefix that the main Teams
# window adds.  Other patterns (1:1, channel names) can also be
# legitimate meeting titles, so we rely on the microphone check
# to confirm we're actually in a call.
_CHAT_PATTERNS = [
    re.compile(r"^Chat \| ", re.IGNORECASE),  # "Chat | …" main window prefix
]


def _is_likely_chat(name: str) -> bool:
    """Return True if the window title is definitely a chat, not a meeting."""
    return any(p.search(name) for p in _CHAT_PATTERNS)


def _get_teams_pids() -> set[int]:
    """Return the set of PIDs for ms-teams processes."""
    pids: set[int] = set()
    try:
        import psutil
        for proc in psutil.process_iter(["name"]):
            if proc.info["name"] and "ms-teams" in proc.info["name"].lower():
                pids.add(proc.pid)
    except Exception:
        pass

    if not pids:
        # Fallback: use ctypes snapshot
        pids = _get_teams_pids_ctypes()

    return pids


def _get_teams_pids_ctypes() -> set[int]:
    """Enumerate processes via CreateToolhelp32Snapshot to find ms-teams."""
    import ctypes
    import ctypes.wintypes

    TH32CS_SNAPPROCESS = 0x00000002

    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.wintypes.DWORD),
            ("cntUsage", ctypes.wintypes.DWORD),
            ("th32ProcessID", ctypes.wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", ctypes.wintypes.DWORD),
            ("cntThreads", ctypes.wintypes.DWORD),
            ("th32ParentProcessID", ctypes.wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", ctypes.wintypes.DWORD),
            ("szExeFile", ctypes.c_char * 260),
        ]

    kernel32 = ctypes.windll.kernel32
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    pids: set[int] = set()
    if snap == -1:
        return pids

    pe = PROCESSENTRY32()
    pe.dwSize = ctypes.sizeof(PROCESSENTRY32)

    if kernel32.Process32First(snap, ctypes.byref(pe)):
        while True:
            name = pe.szExeFile.decode("utf-8", errors="ignore").lower()
            if "ms-teams" in name:
                pids.add(pe.th32ProcessID)
            if not kernel32.Process32Next(snap, ctypes.byref(pe)):
                break

    kernel32.CloseHandle(snap)
    return pids


def _get_meeting_window_titles() -> list[str]:
    """Return titles of visible Teams windows that look like meetings."""
    teams_pids = _get_teams_pids()
    if not teams_pids:
        return []

    titles: list[str] = []

    def _callback(hwnd: int, _lparam: int) -> bool:
        if not _user32.IsWindowVisible(hwnd):
            return True

        length = _user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True

        buf = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value

        if not title.endswith(_TEAMS_SUFFIX):
            return True

        # Check this window belongs to ms-teams
        pid = ctypes.wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in teams_pids:
            return True

        # Strip the suffix to get the meeting/chat name
        name = title[: -len(_TEAMS_SUFFIX)].strip()

        # Filter out chat/channel windows
        if _is_likely_chat(name):
            return True

        if name:
            titles.append(name)

        return True

    _user32.EnumWindows(_EnumWindowsProc(_callback), 0)
    return titles


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@dataclass
class MeetingStatus:
    """Current meeting detection result."""

    in_meeting: bool
    meeting_name: Optional[str] = None


def get_active_meeting() -> MeetingStatus:
    """Detect whether the user is currently in a Teams meeting.

    Returns a MeetingStatus with ``in_meeting=True`` and the meeting name
    if Teams has an active microphone session AND a meeting-titled window
    is open.
    """
    if not _is_teams_mic_active():
        return MeetingStatus(in_meeting=False)

    titles = _get_meeting_window_titles()
    if not titles:
        return MeetingStatus(in_meeting=False)

    # If multiple meeting windows, pick the first (rare edge case)
    return MeetingStatus(in_meeting=True, meeting_name=titles[0])
