"""Copy HTML content to the Windows clipboard (CF_HTML + CF_UNICODETEXT)."""

from __future__ import annotations

import ctypes
import ctypes.wintypes


def _setup_ctypes():
    """Declare Win32 function signatures for 64-bit pointer safety."""
    kernel32 = ctypes.windll.kernel32
    user32 = ctypes.windll.user32

    kernel32.GlobalAlloc.argtypes = [ctypes.wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.restype = ctypes.wintypes.BOOL

    user32.OpenClipboard.argtypes = [ctypes.wintypes.HWND]
    user32.SetClipboardData.argtypes = [ctypes.wintypes.UINT, ctypes.c_void_p]
    user32.SetClipboardData.restype = ctypes.c_void_p


_setup_ctypes()


def copy_html_to_clipboard(html: str, plain_text: str = "") -> None:
    """Put HTML and plain-text on the Windows clipboard.

    Sets CF_HTML so rich editors (Teams, Outlook) render the HTML table,
    and CF_UNICODETEXT as a fallback for plain-text editors.
    """
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    CF_UNICODETEXT = 13
    CF_HTML = user32.RegisterClipboardFormatW("HTML Format")
    GMEM_MOVEABLE = 0x0002

    # --- Build CF_HTML payload ---
    header_template = (
        "Version:0.9\r\n"
        "StartHTML:{start_html:010d}\r\n"
        "EndHTML:{end_html:010d}\r\n"
        "StartFragment:{start_frag:010d}\r\n"
        "EndFragment:{end_frag:010d}\r\n"
    )
    prefix = "<html><body><!--StartFragment-->"
    suffix = "<!--EndFragment--></body></html>"

    dummy_header = header_template.format(
        start_html=0, end_html=0, start_frag=0, end_frag=0
    )
    start_html = len(dummy_header.encode("utf-8"))
    start_frag = start_html + len(prefix.encode("utf-8"))
    end_frag = start_frag + len(html.encode("utf-8"))
    end_html = end_frag + len(suffix.encode("utf-8"))

    header = header_template.format(
        start_html=start_html,
        end_html=end_html,
        start_frag=start_frag,
        end_frag=end_frag,
    )
    payload = (header + prefix + html + suffix).encode("utf-8") + b"\x00"

    # --- Copy to clipboard ---
    if not user32.OpenClipboard(0):
        raise RuntimeError("Cannot open clipboard")
    try:
        user32.EmptyClipboard()

        # HTML format
        hglob = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(payload))
        if not hglob:
            raise RuntimeError("GlobalAlloc failed for HTML")
        ptr = kernel32.GlobalLock(hglob)
        ctypes.memmove(ptr, payload, len(payload))
        kernel32.GlobalUnlock(hglob)
        user32.SetClipboardData(CF_HTML, hglob)

        # Plain-text fallback
        if plain_text:
            text_bytes = plain_text.encode("utf-16-le") + b"\x00\x00"
            hglob_txt = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(text_bytes))
            if hglob_txt:
                ptr_txt = kernel32.GlobalLock(hglob_txt)
                ctypes.memmove(ptr_txt, text_bytes, len(text_bytes))
                kernel32.GlobalUnlock(hglob_txt)
                user32.SetClipboardData(CF_UNICODETEXT, hglob_txt)
    finally:
        user32.CloseClipboard()
