"""Win32 window effects for TokenFloatS: rounded corners and the system
backdrop (acrylic, falling back to mica).

Every call degrades silently: on an older Windows, or when a DWM attribute is
refused, the window simply stays square and opaque. No exception escapes.

Deliberately NOT provided: whole-window translucency. That needs
WS_EX_LAYERED, and a layered window cannot carry a DWM backdrop - the blur is
composited only for non-layered windows. Making the panel layered to honour an
opacity slider would switch the frosted glass off, so the slider was removed
instead and the blur stays on permanently.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt

user32 = ctypes.windll.user32 if hasattr(ctypes, "windll") else None
try:
    dwmapi = ctypes.windll.dwmapi
except OSError:                                    # pragma: no cover
    dwmapi = None

DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_SYSTEMBACKDROP_TYPE = 38

DWMWCP_ROUND = 2
DWMWCP_ROUNDSMALL = 3
DWMSBT_AUTO = 0
DWMSBT_NONE = 1
DWMSBT_MAINWINDOW = 2          # mica
DWMSBT_TRANSIENTWINDOW = 3     # acrylic
DWMSBT_TABBEDWINDOW = 4


def hwnd_of(win) -> int:
    """The real HWND behind a Tk widget (Tk hands out a child window)."""
    if user32 is None:
        return int(win.winfo_id())
    return user32.GetParent(win.winfo_id()) or win.winfo_id()


def set_rounded(hwnd: int, radius: int = 12) -> bool:
    """Round the window corners. True when the DWM path was used.

    DWMWCP_ROUND is deliberately NOT used. On this host it makes DWM paint a
    soft dark glow around the whole window, measured at 30.6 luminance units
    below the bottom edge and reaching ~60px; on a busy desktop that reads as a
    smudge rather than as "floating". DWMWCP_ROUNDSMALL keeps rounded corners and
    measures 3.0 over the same strip, which still separates the panel from what
    is behind it. Measured against a flat white backdrop, one variable at a time:
    ROUND 30.6, ROUNDSMALL 3.0, DO_NOT_ROUND 0.0.

    Note that DWMWA_SHADOW_COLOR and DWMWA_BORDER_COLOR return S_OK here and
    change nothing measurable, so neither is used to control this.
    """
    if user32 is None:
        return False
    if dwmapi is not None:
        pref = wt.DWORD(DWMWCP_ROUNDSMALL)
        try:
            hr = dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE,
                                              ctypes.byref(pref), ctypes.sizeof(pref))
            if hr == 0:
                return True
        except OSError:
            pass
    return _round_via_region(hwnd, radius)


def _round_via_region(hwnd: int, radius: int) -> bool:
    """Fallback: clip the window with a rounded region. Works on every version."""
    try:
        import win32gui
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        w, h = right - left, bottom - top
        if w <= 0 or h <= 0:
            return False
        region = win32gui.CreateRoundRectRgn(0, 0, w + 1, h + 1, radius * 2, radius * 2)
        win32gui.SetWindowRgn(hwnd, region, True)
        return True
    except Exception:                              # noqa: BLE001
        return False


def set_backdrop(hwnd: int, kind: int) -> bool:
    """System backdrop (mica / acrylic). No-op on an unsupported Windows."""
    if dwmapi is None:
        return False
    try:
        value = wt.DWORD(kind)
        hr = dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_SYSTEMBACKDROP_TYPE,
                                          ctypes.byref(value), ctypes.sizeof(value))
        return hr == 0
    except OSError:
        return False


def set_dark_mode(hwnd: int) -> bool:
    if dwmapi is None:
        return False
    try:
        pref = wt.DWORD(1)
        return dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE,
                                            ctypes.byref(pref), ctypes.sizeof(pref)) == 0
    except OSError:
        return False


def capabilities() -> dict:
    """What this machine actually supports, for honest reporting."""
    caps = {"dwm": dwmapi is not None, "backdrop": False, "region": False}
    try:
        import win32gui  # noqa: F401
        caps["region"] = True
    except ImportError:
        pass
    caps["backdrop"] = bool(dwmapi is not None)
    return caps