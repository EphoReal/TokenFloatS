"""TokenFloatS - a tray icon plus an always-on-top floating panel showing
live token usage for Hermes, opencode, Cline, DeepSeek Harness and Codex.

    python tokenfloats.py            # start
    python tokenfloats.py --once     # print a snapshot and exit (no GUI)

Single instance: launching again while one is already running does not start a
second copy - it wakes the existing panel. Without this, double-clicking the
shortcut a second time created a second Tk root and a duplicate tray icon.
"""
from __future__ import annotations

import argparse
import atexit
import math
import os
import sys
import threading
import time
import traceback
import ctypes
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collectors import (COLLECTORS, Usage, collect_all, detect_all,        # noqa: E402
                        finish_counter, load_config, totals)
import wineffects as wex                                                # noqa: E402

try:
    import pystray
    from PIL import Image, ImageDraw, ImageFont
except ImportError as e:                                       # pragma: no cover
    # A windowless launch has nowhere to print to, so drop a note where someone
    # opening the folder will see it, then leave. requirements.txt is the fix.
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "startup-error.log"), "w", encoding="utf-8") as fh:
            fh.write("TokenFloatS could not start: missing dependency.\n")
            fh.write(f"  {type(e).__name__}: {e}\n")
            fh.write("Fix it with:\n")
            fh.write("  venv\\Scripts\\pip install -r requirements.txt\n")
    except OSError:
        pass
    raise

APP = "TokenFloatS"
# How often the sources are re-read. Overridable from config.json as
# {"poll_seconds": 5} - a poll is cheap now that the append-only logs are read
# incrementally (see collectors._codex_file_buckets), so the default is short.
# The floor exists because every poll still stats and globs every store: below a
# couple of seconds that cost stops being worth the responsiveness.
POLL_SECONDS = 10
MIN_POLL_SECONDS = 2


def poll_seconds(config: dict | None = None) -> int:
    """Poll interval in seconds: config first, then the default, never below the floor."""
    cfg = config or {}
    raw = cfg.get("poll_seconds", cfg.get("interval_seconds"))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return POLL_SECONDS
    return max(MIN_POLL_SECONDS, value)


W, H = 600, 760            # expanded: width, and a starting height
W_COLLAPSED = 470          # collapsed bar
H_COLLAPSED = 76
RADIUS = 14                # window corner radius in px

# A hair space between a figure and its unit. Without it "0K" reads as "OK".
# Verified present in every font this app binds (segoeui/segoeuib/msyh/msyhbd).
THIN = "\u2009"

# The headline figure size is shared by the collapsed bar and the expanded
# header so the two states read identically. One constant, not two literals.
FIG_PT = 22

# Palette: a near-black base with one surface level above it and hairline
# separators. The restraint IS the minimalism - one accent per tool, borders
# only where they carry information, no gradients, no shadows, no icons.
BG = "#0e0f12"
BAR = BG                 # the collapsed bar is the base colour, not a card
SURFACE = "#16181d"         # cards sit one step above the base
PANEL = SURFACE          # legacy alias
SURFACE_HI = "#1c1f26"      # hover / active
FG = "#eef0f4"
# Three text tiers, all lifted from the first pass: the old FG_FAINT sat at
# #5c636e, which is only about 2.4:1 against the #0e0f12 base and was hard to
# read for the distribution key, the session counts and the "all"/"run"
# labels. The ramp is preserved, the floor is raised.
FG_DIM = "#b6bcc7"
DIM = FG_DIM            # legacy alias used by the widget code
FG_FAINT = "#868e9b"
LINE = "#2b303a"
ACCENT = {
    "Hermes": "#7aa2f7",
    "opencode": "#9ece6a",
    "Cline": "#e0af68",
    "DeepSeek Harness": "#bb9af7",
    "Codex": "#7dcfff",
    "Claude Code": "#f7768e",
}
BUCKET_COLOR = {
    "input": "#7aa2f7",
    "output": "#9ece6a",
    "reasoning": "#e0af68",
    "cache_read": "#3d4356",
    "cache_write": "#7dcfff",
}
BUCKET_LABEL = {
    "input": "in",
    "output": "out",
    "reasoning": "think",
    "cache_read": "cache",
    "cache_write": "cache w",
}
THRESH = (500_000, 2_000_000)
ORDER = ("Hermes", "opencode", "Cline", "DeepSeek Harness", "Codex", "Claude Code")
BAR_ORDER = ("cache_read", "input", "output", "reasoning", "cache_write")

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tokenfloats.log")


# ------------------------------------------------------------------ logging
def _log(message: str) -> None:
    """Append to tokenfloats.log.

    A windowless launch (pythonw via the .vbs) has no stderr, so any traceback
    would vanish and the user would only ever see a flash and nothing else.
    """
    try:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_FILE, "a", encoding="utf-8", errors="replace") as fh:
            fh.write(f"{stamp} {message}\n")
    except OSError:
        pass


def install_excepthook() -> None:
    def hook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        _log("UNCAUGHT\n" + text)
        sys.__excepthook__(exc_type, exc, tb)
    sys.excepthook = hook

    def thread_hook(args):
        text = "".join(traceback.format_exception(args.exc_type, args.exc_value,
                                                 args.exc_traceback))
        _log(f"THREAD {getattr(args.thread, 'name', '?')}\n{text}")
    threading.excepthook = thread_hook


# ------------------------------------------------------- single-instance lock
_lock_handle = None


def _lock_path() -> str:
    import tempfile
    return os.path.join(tempfile.gettempdir(), "tokenfloats.lock")


def acquire_single_instance() -> bool:
    """Return True when this process owns the lock, False if another copy runs.

    Uses an OS file lock (not a bare PID file): a PID file survives a hard kill
    and would then block a legitimate restart until the PID is reused or the file
    removed by hand. fcntl/msvcrt locks are released by the kernel the moment
    the process dies, so a crash never leaves a stale lock behind.
    """
    global _lock_handle
    path = _lock_path()
    try:
        _lock_handle = open(path, "a+b")
    except OSError as e:
        _log(f"lock open failed: {e}")
        return True                       # never block startup on the lock
    if os.name == "nt":
        import msvcrt
        try:
            msvcrt.locking(_lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            _lock_handle.close()
            _lock_handle = None
            return False
    else:
        import fcntl
        try:
            fcntl.flock(_lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            _lock_handle.close()
            _lock_handle = None
            return False
    _log("lock acquired")
    return True


def release_single_instance() -> None:
    global _lock_handle
    if _lock_handle is None:
        return
    try:
        if os.name == "nt":
            import msvcrt
            _lock_handle.seek(0)
            msvcrt.locking(_lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(_lock_handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        _lock_handle.close()
    except OSError:
        pass
    _lock_handle = None
    _log("lock released")


def wake_existing_instance() -> bool:
    """Bring an already-running panel back to the front.

    Uses ctypes + Win32 ShowWindow rather than a socket: no port to collide on
    and nothing left listening if the process dies.
    """
    if os.name != "nt":
        return False
    try:
        import ctypes
        user32 = ctypes.windll.user32
        found = False
        # EnumWindows over every top-level window, match on our own title
        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def cb(hwnd, _lparam):
            nonlocal found
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            if buf.value == "TokenFloatS":
                found = True
                user32.ShowWindow(hwnd, 9)      # SW_RESTORE
                user32.SetForegroundWindow(hwnd)
                return False
            return True

        user32.EnumWindows(WNDENUMPROC(cb), 0)
        return found
    except Exception as e:                          # noqa: BLE001
        _log(f"wake failed: {e}")
        return False

def human(n: float) -> str:
    """Count in K units: 1.52M -> 1520K. Zero reads as 0K, not a bare 0."""
    if n is None:
        return "-"
    n = float(n)
    if abs(n) < 1000:
        # keep the unit on 0 so a row never renders as a bare "0" next to
        # figures that all carry one
        return f"{int(n)}{THIN}K"
    k = n / 1000
    return f"{k:.1f}{THIN}K" if abs(k) < 100 else f"{k:.0f}{THIN}K"


def load_fonts():
    def f(size, bold=False):
        for name in (("msyhbd.ttc",) if bold else ("msyh.ttc", "segoeui.ttf")):
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                pass
        return ImageFont.load_default()
    return f(13), f(15, True), f(11)


FONT, FONT_B, FONT_S = load_fonts()


def _icon_master() -> Image.Image:
    """The tray mark at 8x, downscaled on use: PIL's line joins and caps are
    poor, and the waves need smooth rounded ends."""
    ss = 8
    n = 64 * ss
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    box = [5 * ss, 5 * ss, (64 - 5) * ss, (64 - 5) * ss]
    d.rounded_rectangle(box, radius=14 * ss, fill=(0, 0, 0, 255))

    x0, x1 = box[0] + 13 * ss, box[2] - 13 * ss
    span = x1 - x0
    steps = 360
    # thickness falls from top to bottom; a small amplitude keeps the three
    # strokes separable at 16px, which is the size the tray actually uses
    for cy, amp, w in ((24.0, 2.4, 5.6), (32.5, 2.0, 4.2), (41.0, 1.6, 3.0)):
        pts = []
        for i in range(steps + 1):
            t = i / steps
            x = x0 + span * t
            y = cy * ss + amp * ss * math.sin(2 * math.pi * t - math.pi / 2)
            pts.append((x, y))
        d.line(pts, fill=BUCKET_COLOR["input"], width=int(round(w * ss)), joint="curve")
        half = w * ss / 2.0
        for px, py in (pts[0], pts[-1]):
            d.ellipse([px - half, py - half, px + half, py + half],
                      fill=BUCKET_COLOR["input"])
    return img


_ICON_MASTER: Image.Image | None = None


def tray_image(spent: int = 0) -> Image.Image:
    """Static tray mark: a rounded black tile with three blue waves.

    Deliberately not a live number. A tray icon has to survive being shrunk to
    16px, and at that size three digits plus a unit become an unreadable smear
    that also repaints every minute.
    """
    global _ICON_MASTER
    if _ICON_MASTER is None:
        _ICON_MASTER = _icon_master()
    return _ICON_MASTER.resize((64, 64), Image.LANCZOS)


def color_for(spent: int) -> str:
    if spent >= THRESH[1]:
        return "#f7768e"
    if spent >= THRESH[0]:
        return "#e0af68"
    return "#9ece6a"


# Both window controls sit on an identically sized chip. Tk sizes widgets from
# their text: width= is in CHARACTERS, so "-" and "x" gave chips of 36x53 and
# 32x44. The chip is therefore an explicit Frame of fixed pixel size with the
# glyph centred on a transparent Label inside it, which Tk cannot resize.
CTL_PX = 30
# Gap between adjacent window controls, identical for every pair.
CTL_GAP = 6


class _Control(tk.Frame):
    """A fixed-size clickable chip carrying one glyph."""

    def __init__(self, parent, text, command, surface, size=15):
        super().__init__(parent, bg=SURFACE_HI, width=CTL_PX, height=CTL_PX,
                         highlightthickness=0, bd=0)
        self.pack_propagate(False)          # honour width/height as pixels
        self._rest_bg = SURFACE_HI
        self._cmd = command
        self._lbl = tk.Label(self, text=text, bg=SURFACE_HI, fg=FG,
                             font=("Segoe UI", size, "bold"), bd=0)
        self._lbl.place(relx=0.5, rely=0.5, anchor="center")
        self._lbl.bind("<Button-1>", lambda _e: self._invoke())
        self._lbl.bind("<Enter>", lambda _e: self._hover(True))
        self._lbl.bind("<Leave>", lambda _e: self._hover(False))
        self.bind("<Button-1>", lambda _e: self._invoke())
        self.bind("<Enter>", lambda _e: self._hover(True))
        self.bind("<Leave>", lambda _e: self._hover(False))
        self.configure(cursor="hand2")

    def _invoke(self):
        self._cmd()

    def _hover(self, on):
        self.configure(bg=FG_DIM if on else self._rest_bg,
                       cursor="hand2")
        self._lbl.configure(bg=FG_DIM if on else self._rest_bg,
                            fg=BG if on else FG)

    def glyph(self) -> str:
        return self._lbl.cget("text")


def _ctl(parent: tk.Misc, text: str, command, surface: str,
         size: int = 15) -> "_Control":
    """Create a window control. `parent` must be passed explicitly: without it
    the widget attaches to the default root, which does not exist here, and the
    control never appears in the panel."""
    return _Control(parent, text, command, surface, size)


# A short explanation of what "this run" measures, shown on hover over the
# little marker beside the label. Tooltip text rather than a permanent line: the
# panel's alignment between the two columns is deliberate, and a wrapped
# paragraph would break it.
RUN_NOTE = (
    "THIS RUN measures tokens consumed since TokenFloatS was opened: the "
    "difference between each tool's running total now and its total when the app "
    "started, so earlier work is not re-counted. It starts at zero on every "
    "launch. One exception, in the other direction: a conversation you RESUME "
    "that the tool no longer lists among its sessions is counted in full the "
    "first time it is seen, so that figure can jump once and then advance "
    "normally. TOTAL is each tool's own lifetime figure, read straight from its "
    "data files."
)


class _Hint(tk.Frame):
    """A small marker that explains a label while the pointer rests on it.

    Built from a Frame of fixed pixel size with the glyph centred inside,
    because Tk's width/height options are in CHARACTERS on every widget type:
    asking a Label for width=15 yields a 109x199 rectangle.
    """

    PX = 15

    def __init__(self, parent, text: str, surface: str):
        super().__init__(parent, bg=surface, width=self.PX, height=self.PX,
                         bd=0, highlightthickness=0, cursor="hand2")
        self.pack_propagate(False)
        self._text = text
        self._surface = surface
        self._glyph = tk.Label(self, text="i", bg=surface, fg=FG_FAINT,
                               font=("Segoe UI", 8, "bold"), bd=0)
        self._glyph.place(relx=0.5, rely=0.5, anchor="center")
        for w in (self, self._glyph):
            w.bind("<Enter>", lambda _e: self._show())
            w.bind("<Leave>", lambda _e: self._hide())
        self._tip: tk.Toplevel | None = None

    def _show(self):
        if self._tip is not None:
            return
        self.configure(bg=FG_FAINT)
        self._glyph.configure(bg=FG_FAINT, fg=BG)
        tip = tk.Toplevel(self)
        tip.overrideredirect(True)
        tip.attributes("-topmost", True)
        box = tk.Frame(tip, bg=SURFACE_HI, highlightthickness=1,
                       highlightbackground=LINE)
        box.pack()
        tk.Label(box, text=self._text, bg=SURFACE_HI, fg=FG_DIM,
                 font=("Segoe UI", 8), justify="left",
                 wraplength=320, padx=9, pady=7).pack()
        tip.update_idletasks()
        x = self.winfo_rootx() + self.winfo_width()
        y = self.winfo_rooty() + self.winfo_height()
        if x + tip.winfo_reqwidth() > tip.winfo_screenwidth():
            x = max(4, self.winfo_rootx() - tip.winfo_reqwidth())
        tip.geometry(f"+{x}+{y}")
        self._tip = tip

    def _hide(self):
        self.configure(bg=self._surface)
        self._glyph.configure(bg=self._surface, fg=FG_FAINT)
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


RUN_NOTE_HISTORY = (
    "Every launch is recorded with its start time, end time and what it went on "
    "to spend, kept in state/runs.json. Opening this list swaps the source rows "
    "for the recorded runs; the panel never grows past the screen. A run still "
    "going reads as now, and an end marked with * means the app was closed or "
    "killed rather than quit, so the end time is when it was next opened."
)


RUN_NOTE_CHART = (
    "Each bar is one launch, as tall as the tokens it went on to spend. A bar "
    "with a dashed edge is the run still in progress. The most recent run is on "
    "the right; scroll left on the chart to reach older ones."
)

# The one colour the chart uses: the same bright blue that stands for
# non-cached input everywhere else in the panel.
BAR_COLOR = "#7aa2f7"


class RunChart(tk.Canvas):
    """One bar per launch, showing only what that run spent.

    Drawn on a Canvas rather than assembled from widgets: a Canvas needs no
    per-column geometry, so a few hundred runs cost the same as a dozen. One
    series, one colour, no per-source split, which keeps the shape readable
    when there are a hundred runs rather than eight.
    """

    # Bars keep a fixed pitch so a label always has room, whatever the count.
    # Fewer bars than fit the width means no scrolling at all.
    MIN_PITCH = 9

    def __init__(self, parent, height=120, surface=BG):
        super().__init__(parent, height=height, bg=surface,
                         highlightthickness=0, bd=0)
        self.surface = surface
        self.runs: list = []
        self._xs: list[float] = []
        self._tip: tk.Label | None = None

        # ---- horizontal browsing
        self._offset = 0          # runs hidden to the left, newest still pinned
        self._slot = 0.0
        self._on_scroll = None

        # No button bindings here on purpose. The panel drags itself with
        # <ButtonPress-1>/<B1-Motion>, and a chart that also took them fought the
        # window for the drag: moving over the chart moved the panel. The wheel
        # needs no button and cannot collide.
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", self._hide_tip)
        self.bind("<MouseWheel>", self._wheel, add="+")
        self.bind("<Shift-MouseWheel>", self._wheel, add="+")

    def set_runs(self, runs):
        self.runs = runs or []
        self.bind("<Configure>", self.redraw, add="+")
        self.after_idle(self.redraw)
        self.redraw()

    def _on_motion(self, e):
        if not self._xs or not self.runs:
            return
        xs = self._xs
        # nearest bar centre
        k = min(range(len(xs)), key=lambda i: abs(xs[i] - e.x))
        if abs(xs[k] - e.x) > max(6.0, min(self._slot / 2, 22.0)):
            self._hide_tip()
            return
        # xs is the visible window, so map back to the whole record
        r = self.runs[self._offset + k]
        x = xs[k]
        spent = human(r.get("total", 0))
        when = r.get("start", "")
        live = not r.get("end")
        text = f"{when}   {spent}" + ("   running" if live else "")
        self._show_tip(text, x)

    def _show_tip(self, text, x):
        if self._tip is None:
            self._tip = tk.Label(self, text="", bg=SURFACE_HI, fg=FG,
                                 font=("Segoe UI", 8), bd=0, padx=5, pady=2)
        self._tip.configure(text=text)
        self._tip.place(x=max(0, min(x + 8, self.winfo_width() - 140)),
                        y=2, anchor="nw")
        self._tip.lift()

    def _hide_tip(self, _e=None):
        if self._tip is not None:
            self._tip.place_forget()

    # ---- horizontal browsing
    def _visible_count(self, plot_w: float) -> int:
        """How many bars fit at the fixed pitch."""
        return max(1, int(plot_w // self.MIN_PITCH))

    def _max_offset(self, plot_w: float) -> int:
        return max(0, len(self.runs) - self._visible_count(plot_w))

    def _wheel(self, e):
        """Scroll left for older runs, right for newer.

        Three bars a notch: enough to cross a day in one go, small enough that a
        single flick does not throw the view past what is on screen.
        """
        step = 3 if e.delta > 0 else -3
        self._scroll_to(self._offset + step)

    def _scroll_to(self, value: float):
        plot_w = max(1, self.winfo_width() - 4)
        top = self._max_offset(plot_w)
        new = int(round(max(0, min(top, value))))
        if new != self._offset:
            self._offset = new
            self._hide_tip()
            self.redraw()
            if self._on_scroll:
                self._on_scroll(new, top)

    def scroll_by(self, delta: int):
        self._scroll_to(self._offset + delta)

    def to_latest(self):
        self._scroll_to(0)

    def redraw(self, _e=None):
        # A Canvas reports 1x1 until it is mapped, so the first draw has no size
        # to work with. Redraw as soon as a real one arrives.
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w <= 1 or h <= 1 or not self.runs:
            return

        pad_x, pad_top = 2, 4
        day_h, time_h = 11, 14          # strips: dates above, times below
        axis_h = day_h + time_h
        plot_h = max(1, h - pad_top - axis_h)
        base_y = pad_top + plot_h
        plot_w = max(1, w - pad_x * 2)

        # The window onto the record: the newest runs pinned to the right, older
        # ones reached by dragging left. The pitch is fixed so a label always
        # has room, which means only as many bars are drawn as fit.
        per = self._visible_count(plot_w)
        n = len(self.runs)
        # Use the width available when the record fits, and only drop to the
        # fixed pitch once it does not. Spreading six bars across 560px looked
        # sparse and pushed the labels into thinning.
        slot = plot_w / min(per, n)
        slot = max(self.MIN_PITCH, slot)
        bar_w = max(1.5, min(slot - 1.5, 18.0))
        self._slot = slot

        top = max(0, n - per)
        self._offset = max(0, min(self._offset, top))

        peak = max((r.get("total", 0) for r in self.runs), default=0) or 1

        # The window, newest last. Index k here is an absolute position into
        # self.runs, counted from the newest.
        first = self._offset
        window = list(self.runs[first:first + per])
        xs = [pad_x + plot_w - (len(window) - i - 0.5) * slot
              for i in range(len(window))]
        self._xs = xs
        runs = window

        for i, r in enumerate(runs):
            x = xs[i]
            total = r.get("total", 0)
            bh = 0 if not total else max(1.5, (total / peak) * plot_h)
            y = base_y - bh
            live = not r.get("end")
            self.create_rectangle(x - bar_w / 2, y, x + bar_w / 2, base_y,
                                  fill=BAR_COLOR if total else LINE,
                                  outline="")
            if live:
                # the run still going, so the edge stays open
                self.create_line(x + bar_w / 2 + 1.5, y, x + bar_w / 2 + 1.5,
                                 base_y, fill=FG_FAINT, dash=(1, 2))

        # baseline, so the bars sit on something
        self.create_line(pad_x, base_y, pad_x + plot_w, base_y, fill=LINE)

        self._draw_time_axis(runs, xs, base_y, w, pad_top)

    def _draw_time_axis(self, runs, xs, base_y, canvas_w, pad_top):
        """Dates above the bars, times below, thinned until they fit.

        Each run is timestamped with the moment the app was opened, so this is
        what the bars are measured against. Labelling every bar would collide at
        any useful number of runs, so labels are dropped evenly instead of being
        squeezed together.

        runs is newest first and xs is indexed the same way, so bar xs[k] sits at
        x[k]; the day groups are therefore built over k and walked backwards to
        lay them out oldest on the left.
        """
        n = len(runs)
        dates = [r.get("start", "")[:10] for r in runs]
        times = [r.get("start", "")[11:16] for r in runs]

        # groups over k, then reversed so each day reads left to right
        groups: list[list] = []           # [date, first_k, last_k]
        for k in range(n):
            d = dates[k]
            if not groups or groups[-1][0] != d:
                groups.append([d, k, k])
            else:
                groups[-1][2] = k
        groups.reverse()                 # now oldest day first, k ascending

        # ---- a faint rule where one day ends and the next begins
        for _d, lo_k, _hi_k in groups[:-1]:
            edge = (xs[lo_k] + xs[lo_k - 1]) / 2 if lo_k > 0 else None
            if edge is not None:
                self.create_line(edge, pad_top, edge, base_y,
                                 fill=LINE, dash=(1, 3))

        # ---- one date label per day, where that day is wide enough to hold one.
        # With a hundred runs each day is only a few pixels wide, so the labels
        # thin out and disappear; the range in the corner is what stays readable.
        for d, lo_k, hi_k in groups:
            left = xs[hi_k]
            right = xs[lo_k]
            pitch = abs(xs[1] - xs[0]) if n > 1 else canvas_w
            width = abs(right - left) + pitch
            cx = (left + right) / 2
            label = d[5:] if d else ""      # 10-03, not 2026-10-03
            if width >= 30:
                self.create_text(cx, 1, text=label, anchor="n",
                                 fill=FG_DIM, font=("Segoe UI", 7, "bold"))
            elif width >= 15:
                self.create_text(cx, 1, text=label, anchor="n",
                                 fill=FG_FAINT, font=("Segoe UI", 7))

        # The date range only matters once the per-day labels thin out, which
        # is also when the first day's label is gone from the left edge. Draw it
        # only then, so it cannot sit on top of one.
        days = [g[0] for g in groups if g[0]]
        if len(days) > 1:
            # Only where the date row is genuinely empty. The first day's label
            # sits at the left edge when its group is wide enough, so the range
            # takes that spot only when no label claimed it.
            g0 = groups[0]
            g0_wide = abs(xs[g0[1]] - xs[g0[2]]) if g0[1] != g0[2] else 0
            labelled_left = g0_wide >= 30
            if not labelled_left:
                self.create_text(0, 1,
                                 text=f"{days[0][5:]} - {days[-1][5:]}",
                                 anchor="nw", fill=FG_FAINT,
                                 font=("Segoe UI", 7))

        # ---- times, thinned by measured width. Pitch alone is not enough: the
        # leftmost and rightmost labels hang over the canvas edge, so the first
        # and last are anchored inward.
        if n > 1:
            slot = abs(xs[1] - xs[0])
        else:
            slot = canvas_w
        # Walk from the left edge and place a label only where the one before it
        # has actually cleared. Thinning by pitch alone left two labels touching
        # at the right, where the last is shifted inward to stay on the canvas.
        # k counts from the newest, so k == 0 is the rightmost bar and k == n-1
        # the leftmost. Walk from the right: the newest label is the one the user
        # reads first, and older ones are dropped as they would collide.
        # xs runs left to right over the window, and the window is newest-first,
        # so xs[0] is the OLDEST bar at the left edge and xs[n-1] the newest at
        # the right. Walk left to right, keeping a label only where the previous
        # one has cleared.
        label_w, gap = 26, 4
        left_edge = None
        for k in range(n):
            if k == 0:
                ax, x0 = xs[0], xs[0]                   # left edge, inward
            elif k == n - 1:
                ax, x0 = xs[k], xs[k] - label_w         # right edge, inward
            else:
                ax = xs[k]
                x0 = ax - label_w / 2
            if x0 < 0 or x0 + label_w > canvas_w:
                continue
            if left_edge is not None and x0 < left_edge + gap:
                continue
            left_edge = x0 + label_w
            self.create_text(ax, base_y + 4, text=times[k], anchor="w",
                             fill=FG_FAINT, font=("Segoe UI", 7))


class RunHistory(tk.Frame):
    """The recorded runs, drawn in place of the per-source rows.

    The panel already fills a 1080px screen, so a list appended below the
    source rows cannot fit, and a popup would break the single-window idea.
    Instead this takes over the rows area while it is open, and hands it back
    when closed. Only the summary line lives in the normal layout.
    """

    MAX_ROWS = 60
    ROW_H = 21            # measured: a 9pt row with pady=1
    SOURCES_PER_ROW = 2   # the stamps take most of the row's width
    STRIP_H = 18          # measured: the scroll strip, one 7pt line

    def __init__(self, parent, surface=BG):
        super().__init__(parent, bg=surface)
        self.surface = surface
        self.runs: list = []
        self._rows_h = 0             # the rows' height, kept while they are away

        cap = tk.Frame(self, bg=surface)
        cap.pack(fill="x")
        tk.Label(cap, text="RUNS", bg=surface, fg=FG_FAINT,
                 font=("Segoe UI", 8, "bold")).pack(side="left")
        _Hint(cap, RUN_NOTE_HISTORY, surface).pack(side="left", padx=(7, 0))
        self.lbl_summary = tk.Label(cap, text="", bg=surface, fg=FG_DIM,
                                    font=("Segoe UI", 9), cursor="hand2")
        self.lbl_summary.pack(side="left", padx=(14, 0))
        self.lbl_toggle = tk.Label(cap, text="\u25be", bg=surface, fg=FG_FAINT,
                                   font=("Segoe UI", 9), cursor="hand2")
        self.lbl_toggle.pack(side="right")
        for w in (self.lbl_summary, self.lbl_toggle):
            w.bind("<Button-1>", lambda _e: self.toggle())
        self.lbl_toggle.bind("<Enter>", lambda _e: self.lbl_toggle.configure(
            fg=FG_DIM))
        self.lbl_toggle.bind("<Leave>", lambda _e: self.lbl_toggle.configure(
            fg=FG_FAINT))

        # the chart and list are built on demand and parked out of sight
        self.body = tk.Frame(parent, bg=surface)

    # ---- data
    def reload(self):
        try:
            import collectors
            self.runs = collectors.recent_runs(self.MAX_ROWS)
        except Exception:                              # noqa: BLE001
            self.runs = []
        self._paint_summary()
        if self.is_open():
            self.show()
        return self.runs

    def _paint_summary(self):
        if not self.runs:
            self.lbl_summary.configure(text="no runs recorded yet")
            self.lbl_toggle.configure(text="")
            return
        done = [r for r in self.runs if r.get("end")]
        days = {r["start"][:10] for r in self.runs}
        spent = sum(r.get("total", 0) for r in done)
        self.lbl_summary.configure(
            text=f"{len(self.runs)} recorded   {len(days)} day"
                 f"{'s' if len(days) != 1 else ''}   "
                 f"{human(spent)} in finished runs")
        self.lbl_toggle.configure(text="\u25be")

    # ---- open / close, swapping with the source rows
    def is_open(self) -> bool:
        return self.body.winfo_manager() != ""

    def toggle(self):
        if self.is_open():
            self.hide()
        else:
            self.show()

    def show(self):
        area = self.owner.rows.get("__area__")
        # Measure the source rows while they are still there: once the frame
        # takes their place the figure is gone. Letting the window shrink on open
        # reads as a jump, so the history claims exactly their space.
        # Measure the source rows while they are still there: once the frame
        # takes their place the figure is gone. Remember it, because a refresh
        # while the history is open rebuilds the body and would otherwise have
        # nothing to measure against and would come out short.
        rows_h = area.winfo_reqheight() if area is not None else 0
        if not self._rows_h:
            # recorded once: the area shrinks as it is rebuilt, so re-reading it
            # on every refresh walked the frame down a little each time
            self._rows_h = rows_h if rows_h > 1 else 0
        else:
            rows_h = self._rows_h
        if rows_h <= 1:
            rows_h = self.winfo_reqheight()
        rows_w = (area.winfo_reqwidth() if area is not None else 0) or (W - 40)

        # Pin the frame to that height before anything is built inside it, so the
        # row budget below can measure against a real figure.

        self.body.configure(height=rows_h, width=rows_w)
        self.body.pack_propagate(False)
        self.body.pack(fill="x", padx=20, pady=(0, 14))
        if area is not None and area.winfo_manager():
            area.pack_forget()          # the list takes the rows' place
        self._paint_list()
        self.lbl_toggle.configure(text="\u25b4")
        self._update_strip()

    def hide(self):
        self.body.pack_forget()
        area = self.owner.rows.get("__area__")
        if area is not None and not area.winfo_manager():
            area.pack(fill="both", expand=True, padx=20, pady=(0, 14))
        self.lbl_toggle.configure(text="\u25be")

    def _paint_list(self):
        for c in self.body.winfo_children():
            c.destroy()

        # ---- the chart, then the list
        self.chart = RunChart(self.body, height=120, surface=self.surface)
        self.chart.pack(fill="x", pady=(0, 4))
        self.chart.set_runs(self.runs)

        # A strip under the chart: where in the record we are, and a way back to
        # the newest runs. Hidden when everything already fits.
        self.bar_strip = tk.Frame(self.body, bg=self.surface)
        self.lbl_where = tk.Label(self.bar_strip, text="", bg=self.surface,
                                  fg=FG_FAINT, font=("Segoe UI", 7))
        self.lbl_where.pack(side="left")
        self.btn_latest = tk.Label(self.bar_strip, text="", bg=self.surface,
                                   fg=FG_FAINT, font=("Segoe UI", 7),
                                   cursor="hand2")
        self.btn_latest.pack(side="right")
        self.btn_latest.bind("<Button-1>", self._goto_latest)
        self.chart._on_scroll = self._on_chart_scroll
        # Pack it now, before the rows are built: _visible() measures what is
        # left in the frame, and an unpacked strip reads as zero-height, which
        # let the rows overflow and push the strip out of sight. It is hidden
        # again straight after if there is nothing to scroll.
        self.bar_strip.pack(fill="x", pady=(0, 10))
        self.after_idle(self._update_strip)

        hdr = tk.Frame(self.body, bg=self.surface)
        hdr.pack(fill="x", pady=(0, 6))
        # A stamp is 16 characters ("2026-10-03 04:35"); anything narrower and
        # the column loses its tail.
        for text, w, side in (("START", 17, "left"), ("END", 17, "left"),
                              ("SPENT", 9, "left"), ("BY SOURCE", 0, "right")):
            tk.Label(hdr, text=text, bg=self.surface, fg=FG_FAINT,
                     font=("Segoe UI", 7, "bold"), width=w,
                     anchor="w" if side == "left" else "e").pack(side=side)

        if not self.runs:
            tk.Label(self.body, text="nothing recorded yet", bg=self.surface,
                     fg=FG_FAINT, font=("Segoe UI", 9)).pack(anchor="w")
            return

        # only as many rows as the rows area can hold, newest first
        visible = self._visible()
        for r in self.runs[:visible]:
            row = tk.Frame(self.body, bg=self.surface)
            row.pack(fill="x", pady=1)
            live = not r.get("end")
            fg = FG if live else FG_DIM
            tk.Label(row, text=r["start"], bg=self.surface, fg=fg,
                     font=("Segoe UI", 9), width=17, anchor="w").pack(side="left")
            # A run that ended without a clean exit is marked, so a kill does
            # not read as a deliberate stop.
            when = "now" if live else (r["end"] + "*" if r.get("unclean")
                                       else r["end"])
            tk.Label(row, text=when, bg=self.surface,
                     fg=FG_FAINT if live else fg,
                     font=("Segoe UI", 9), width=17, anchor="w").pack(side="left")
            tk.Label(row, text=human(r.get("total", 0)), bg=self.surface, fg=fg,
                     font=("Segoe UI", 9, "bold"), width=9,
                     anchor="e").pack(side="left")
            src = r.get("by_source") or {}
            top = sorted(src.items(), key=lambda kv: -kv[1])
            # Only what fits the width left over: the stamps take most of it, and
            # a truncated list is worse than a short one with a count.
            limit = self.SOURCES_PER_ROW
            txt = "  ".join(f"{n.split()[0]} {human(v)}"
                            for n, v in top[:limit])
            if len(src) > limit:
                txt += f"  +{len(src) - limit}"
            tk.Label(row, text=txt or "-", bg=self.surface, fg=FG_FAINT,
                     font=("Segoe UI", 8), anchor="w").pack(side="left",
                                                            padx=(8, 0))

        hidden = len(self.runs) - visible
        if hidden > 0:
            tk.Label(self.body, text=f"+ {hidden} older run"
                                     f"{'s' if hidden != 1 else ''} in "
                                     f"state/runs.json",
                     bg=self.surface, fg=FG_FAINT,
                     font=("Segoe UI", 8)).pack(anchor="w", pady=(4, 0))

    def _goto_latest(self, _e=None):
        """Back to the newest runs. Named, not a lambda, so it survives the
        chart being rebuilt and cannot fail silently."""
        self.chart.to_latest()
        self._update_strip()

    def _on_chart_scroll(self, offset, top):
        self._update_strip(offset, top)

    def _update_strip(self, offset=None, top=None):
        """Say how much of the record is off to the left, and offer the way back.

        Hidden entirely when everything fits: a strip that cannot scroll is just
        noise, and the panel has little room to spare.
        """
        c = self.chart
        if offset is None:
            offset = c._offset
        if top is None:
            # the chart is measured after it is packed, so ask it to lay out
            c.update_idletasks()
            width = c.winfo_width()
            if width <= 1:
                return                       # not measured yet; try again later
            top = c._max_offset(width - 4)
        if not top or not c.runs:
            if self.bar_strip.winfo_manager():
                self.bar_strip.pack_forget()
            return
        if not self.bar_strip.winfo_manager():
            self.bar_strip.pack(fill="x", pady=(0, 10))
        shown = min(len(c.runs), c._visible_count(max(1, c.winfo_width() - 4)))
        self.lbl_where.configure(
            text=f"{shown} of {len(c.runs)} runs   "
                 f"scroll left for older")
        self.btn_latest.configure(
            text="latest \u2192" if offset else "latest",
            fg=FG_DIM if offset else FG_FAINT)

    def _visible(self) -> int:
        """Rows that fit beside the chart, inside the frame's fixed height.

        The rows share the frame with the chart and the scroll strip, so what is
        left after those is what the rows get. Measuring the source rows instead
        overflowed the panel and pushed the strip out of sight.
        """
        self.update_idletasks()
        avail = self.body.winfo_height()
        if avail <= 1:
            area = self.owner.rows.get("__area__")
            avail = area.winfo_reqheight() if area is not None else 300
        used = 0
        for c in self.body.winfo_children():
            if isinstance(c, tk.Label):
                continue
            if isinstance(c, RunChart):
                used += c.winfo_reqheight() + 6
            elif c is getattr(self, "bar_strip", None):
                # created above but packed later, so its height is still 1
                used += max(c.winfo_reqheight(), self.STRIP_H) + 10
            else:
                used += c.winfo_reqheight() + 2
        # one line for the column header, then the rows
        left = avail - used - self.ROW_H - 6
        return max(3, min(self.MAX_ROWS, int(left // (self.ROW_H + 2))))


def make_bar(host: tk.Frame, parts: dict, total: int, height: int = 3, padx: int = 0,
             surface: str = SURFACE):
    """Draw a hairline stacked bar plus a single line of inline key text.

    Minimalism over a legend grid: the bar is the shape, the numbers ride
    underneath in one dim line, so each row costs one line instead of two or
    three. Only non-zero buckets are named.

    The host is never given a fixed height: a hard 22px slot clipped this text
    by 13px and it bled into the next row. Tk must measure it instead.
    """
    for child in host.winfo_children():
        child.destroy()

    bar = tk.Canvas(host, height=height, bg=surface, highlightthickness=0, bd=0)
    bar.pack(fill="x", padx=padx, pady=(9, 0))
    host.update_idletasks()
    w = max(bar.winfo_width() or 520, 40)
    x = 0.0
    for key in BAR_ORDER:
        v = parts.get(key, 0)
        if not v or not total:
            continue
        seg = w * (v / total)
        # Skip anything under a pixel: a 0% bucket otherwise paints a visible
        # sliver that contradicts the "0%" printed underneath it.
        if seg < 1.0:
            continue
        bar.create_rectangle(x, 0, x + seg, height, fill=BUCKET_COLOR[key], outline="")
        x += seg

    shown = [k for k in BAR_ORDER if parts.get(k, 0)]
    if not shown or not total:
        # keep the slot the same height as a populated row so the rhythm holds
        tk.Frame(host, height=20, bg=surface).pack(fill="x", padx=padx)
        return
    bits = []
    for key in shown:
        bits.append(f"{BUCKET_LABEL[key]} {parts[key] / total * 100:.0f}%")
    tk.Label(host, text="   ".join(bits), bg=surface, fg=FG_DIM,
             font=("Segoe UI", 9), anchor="w").pack(fill="x", padx=padx, pady=(6, 0))


def rule(parent, surface: str = BG, padx: int = 20, pady=(0, 0)):
    """A hairline separator. Tk has no borderless rule, so use a 1px frame."""
    tk.Frame(parent, bg=LINE, height=1).pack(fill="x", padx=padx, pady=pady)


def dot(parent, color: str, size: int = 6):
    return tk.Frame(parent, bg=color, width=size, height=size)


class Panel(tk.Tk):
    """Two-state always-on-top panel.

    collapsed - a slim bar with this run's total and the lifetime total
    expanded  - the full per-harness breakdown plus the distribution bars
    """

    def __init__(self, on_close_request):
        super().__init__()
        self.on_close_request = on_close_request
        self.overrideredirect(True)
        # overrideredirect windows have no caption, so the class name ("tk") is
        # what the window manager shows. Set an explicit title so wake_existing
        # -instance() can find this window by name.
        self.title(APP)
        self.geometry(f"{W_COLLAPSED}x{H_COLLAPSED}+40+40")
        self.configure(bg=BG)
        self.last_usage: list[Usage] = []
        self.drag_off = (0, 0)
        # Start expanded: the full breakdown is the reason to have this on
        # screen at all, and hiding it behind a second click is a worse default.
        self.expanded = True
        self._hwnd = 0
        self._blur_ok = False
        self._build_collapsed()
        self._build_expanded()
        # the bar is built first and packed first; unpack it so the panel is
        # what shows at launch
        self.bar.pack_forget()
        self.detail.pack(fill="both", expand=True)
        self.geometry(f"{W}x{H}+40+40")
        self._apply_effects()
        # Always on top: a usage panel that can be buried is useless.
        self.attributes("-topmost", True)
        self.bind("<ButtonPress-1>", self.start_drag)
        self.bind("<B1-Motion>", self.do_drag)
        self.bind("<Escape>", lambda _e: self.collapse())

    # ------------------------------------------------------------ appearance
    def _apply_effects(self):
        """Apply rounding and the system backdrop once the HWND exists.

        The panel is deliberately NOT made layered: a layered window cannot carry
        a DWM backdrop (the blur is composited only for non-layered windows), so
        staying non-layered keeps the frosted glass permanently on. There is
        deliberately no opacity control - see the README.
        """
        try:
            self.update_idletasks()
            self._hwnd = int(self.winfo_id())
            parent = ctypes.windll.user32.GetParent(self._hwnd)
            if parent:
                self._hwnd = parent
        except Exception as e:                          # noqa: BLE001
            _log(f"no hwnd for effects: {e}")
            return
        ok_round = wex.set_rounded(self._hwnd, RADIUS)
        wex.set_dark_mode(self._hwnd)
        self._blur_ok = wex.set_backdrop(self._hwnd, wex.DWMSBT_TRANSIENTWINDOW)
        if not self._blur_ok:
            self._blur_ok = wex.set_backdrop(self._hwnd, wex.DWMSBT_MAINWINDOW)
        _log(f"effects: rounded={ok_round} blur={self._blur_ok}")

    # ------------------------------------------------------------ collapsed bar
    def _build_collapsed(self):
        """One line, no card: label above, figure below, in the base colour."""
        self.bar = tk.Frame(self, bg=BAR)
        self.bar.pack(fill="both", expand=True)

        left = tk.Frame(self.bar, bg=BAR)
        left.pack(side="left", fill="y", padx=(20, 0), pady=12)
        cap = tk.Frame(left, bg=BAR)
        cap.pack(anchor="w")
        tk.Label(cap, text="THIS RUN", bg=BAR, fg=FG_FAINT,
                 font=("Segoe UI", 8, "bold")).pack(side="left")
        _Hint(cap, RUN_NOTE, BAR).pack(side="left", padx=(7, 0))
        self.lbl_bar_run = tk.Label(left, text="-", bg=BAR, fg=FG,
                                      font=("Segoe UI", 22, "bold"))
        self.lbl_bar_run.pack(anchor="w")

        tk.Frame(self.bar, bg=LINE, width=1, height=26).pack(side="left",
                                                              fill="y", padx=(18, 18))

        mid = tk.Frame(self.bar, bg=BAR)
        mid.pack(side="left", fill="y", pady=12)
        tk.Label(mid, text="TOTAL", bg=BAR, fg=FG_FAINT,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w")
        self.lbl_bar_total = tk.Label(mid, text="-", bg=BAR, fg=FG_DIM,
                                      font=("Segoe UI", 22))
        self.lbl_bar_total.pack(anchor="w")

        right = tk.Frame(self.bar, bg=BAR)
        right.pack(side="right", fill="y", padx=(0, 14), pady=12)
        # the bar cannot collapse further, so its first control expands
        # close on the RIGHT, expand on its left - identical placement to the
        # expanded header, so nothing jumps when the state changes
        _ctl(right, "+", self.expand, BAR).pack(side="left", padx=(0, CTL_GAP))
        _ctl(right, "\u00d7", self.on_close_request, BAR).pack(side="left")

    # ------------------------------------------------------------ expanded view
    def _build_expanded(self):
        self.detail = tk.Frame(self, bg=BG)
        # starts hidden

        head = tk.Frame(self.detail, bg=BG)
        head.pack(fill="x", padx=20, pady=(18, 0))
        tk.Label(head, text=APP, bg=BG, fg=FG_DIM,
                 font=("Segoe UI", 10, "bold")).pack(side="left")
        # pack(side="right") anchors the FIRST packed control against the right
        # edge, so close is packed first to sit outermost; collapse goes after
        # it, to the left. Verified by rootx: close 584, collapse 554.
        # pack(side="right") fills from the right edge inward, so the gap
        # BETWEEN two of these is the LEFT padx of the left-hand one.
        _ctl(head, "\u00d7", self.on_close_request, BG).pack(side="right",
                                                              padx=(CTL_GAP, 0))
        _ctl(head, "\u2212", self.collapse, BG).pack(side="right",
                                                     padx=(CTL_GAP, 0))
        self.lbl_stamp = tk.Label(self.detail, text="", bg=BG, fg=FG_FAINT,
                                  font=("Segoe UI", 9))
        self.lbl_stamp.pack(anchor="w", padx=20, pady=(4, 0))

        # ---- grand total, same shape as the collapsed bar: THIS RUN first, then
        # TOTAL, each a small label above its figure at the same size. The bar is
        # the compact version of this, so the two states now read the same way.
        head2 = tk.Frame(self.detail, bg=BG)
        head2.pack(fill="x", padx=20, pady=(18, 0))

        run_col = tk.Frame(head2, bg=BG)
        run_col.pack(side="left", fill="y", anchor="center")
        cap = tk.Frame(run_col, bg=BG)
        cap.pack(anchor="w")
        tk.Label(cap, text="THIS RUN", bg=BG, fg=FG_FAINT,
                 font=("Segoe UI", 8, "bold")).pack(side="left")
        _Hint(cap, RUN_NOTE, BG).pack(side="left", padx=(7, 0))
        self.lbl_run = tk.Label(run_col, text="-", bg=BG, fg=FG,
                                  font=("Segoe UI", FIG_PT, "bold"))
        self.lbl_run.pack(anchor="w", pady=(2, 0))
        self.lbl_sessions = tk.Label(run_col, text="", bg=BG, fg=FG_DIM,
                                     font=("Segoe UI", 9))
        self.lbl_sessions.pack(anchor="w", pady=(2, 0))

        tk.Frame(head2, bg=LINE, width=1).pack(side="left", fill="y",
                                                padx=(20, 20))

        total_col = tk.Frame(head2, bg=BG)
        total_col.pack(side="left", fill="y", anchor="center")
        tk.Label(total_col, text="TOTAL", bg=BG, fg=FG_FAINT,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w")
        self.lbl_total = tk.Label(total_col, text="-", bg=BG, fg=FG_DIM,
                                  font=("Segoe UI", FIG_PT))
        self.lbl_total.pack(anchor="w", pady=(2, 0))

        self.tot_bar = tk.Frame(self.detail, bg=BG)
        self.tot_bar.pack(fill="x", padx=20, pady=(10, 0))

        rule(self.detail, padx=20, pady=(16, 14))

        # ---- per-source rows. Kept by name: the run history swaps this area
        # out for its list, so it needs a handle to hand it back.
        # ---- recorded runs. Between the totals and the source rows, so it
        # sits directly under the totals in both states: it used to be last,
        # which put it at the bottom of the expanded panel and made it appear to
        # jump to the top once the panel was collapsed and expanded again.
        rule(self.detail, padx=20, pady=(0, 8))
        self.history = RunHistory(self.detail, BG)
        self.history.owner = self
        self.history.pack(fill="x", padx=20, pady=(0, 12))
        self.history.reload()

        self.rows: dict[str, tk.Frame] = {}
        scroll = tk.Frame(self.detail, bg=BG)
        scroll.pack(fill="both", expand=True, padx=20, pady=(0, 14))
        self.rows["__area__"] = scroll
        self.blocks: dict[str, dict] = {}
        for name in ORDER:
            self._source_block(scroll, name)

    def _source_block(self, parent, name):
        """One row per tool: name and figure on one line, hairline bar beneath.

        The accent is a 2px marker on the left edge rather than coloured text,
        so the eye reads the numbers first and the identity second.
        """
        box = tk.Frame(parent, bg=BG)
        box.pack(fill="x", pady=(0, 16))
        c = ACCENT.get(name, FG_DIM)
        row = tk.Frame(box, bg=BG)
        row.pack(fill="x")

        mark = tk.Frame(row, bg=c, width=2, height=30)
        mark.pack(side="left", fill="y", padx=(0, 12))

        names = tk.Frame(row, bg=BG)
        names.pack(side="left", fill="y")
        lbl_name = tk.Label(names, text=name, bg=BG, fg=FG,
                            font=("Microsoft YaHei UI", 10), anchor="w")
        lbl_name.pack(anchor="w")
        lbl_sess = tk.Label(names, text="", bg=BG, fg=FG_FAINT,
                            font=("Segoe UI", 9), anchor="w")
        lbl_sess.pack(anchor="w", pady=(2, 0))

        # Order matters here. "all <figure>   run <figure>" reads as if "all"
        # modifies the number to its left and "run" modifies the one to its
        # right, which is exactly backwards. Putting the UNIT word first and the
        # metric word last makes each pair read left to right:
        #     all 1779253K        run 294124K
        fig = tk.Frame(row, bg=BG)
        fig.pack(side="right")
        tk.Label(fig, text="all", bg=BG, fg=FG_FAINT,
                 font=("Segoe UI", 9)).pack(side="left", padx=(0, 6))
        lbl_fig = tk.Label(fig, text="", bg=BG, fg=FG,
                           font=("Segoe UI", 15, "bold"), anchor="e")
        lbl_fig.pack(side="left")

        lbl_run = tk.Label(row, text="", bg=BG, fg=FG_DIM,
                             font=("Segoe UI", 10), anchor="e")
        lbl_run.pack(side="right", padx=(0, 22))

        barhost = tk.Frame(box, bg=BG)
        barhost.pack(fill="x")
        self.blocks[name] = dict(box=box, fig=lbl_fig, run=lbl_run,
                                 sess=lbl_sess, bar=barhost,
                                 mark=mark, names=lbl_name)

    # ------------------------------------------------------------ state switch
    def expand(self):
        """Swap the bar for the full panel, keeping the corner on screen."""
        if self.expanded:
            return
        self.expanded = True
        x, y = self.winfo_x(), self.winfo_y()
        sw = self.winfo_screenwidth()
        keep = min(max(x, 0), max(0, sw - W))      # never push it off-screen
        self.bar.pack_forget()
        self.detail.pack(fill="both", expand=True)
        self.geometry(f"{W}x{H}+{keep}+{y}")
        self.refresh(self.last_usage)
        self.update_idletasks()
        self._autosize()
        self.lift()

    def collapse(self):
        if not self.expanded:
            return
        self.expanded = False
        x, y = self.winfo_x(), self.winfo_y()
        self.detail.pack_forget()
        self.bar.pack(fill="both", expand=True)
        self.geometry(f"{W_COLLAPSED}x{H_COLLAPSED}+{x}+{y}")
        self._apply_effects()
        self.lift()

    def start_drag(self, e):
        self.drag_off = (e.x_root - self.winfo_x(), e.y_root - self.winfo_y())

    def do_drag(self, e):
        self.geometry(f"+{e.x_root - self.drag_off[0]}+{e.y_root - self.drag_off[1]}")

    def _autosize(self):
        """Grow the window to fit its content, without letting it move.

        A hard-coded height silently clips the last row (Codex was cut off at
        700px when the content needs 803px), so measure instead of guessing.

        geometry() with no position keeps the top-left corner, so a bare
        height change slides the bottom of the panel and the panel appears to
        jump up the screen. Keep the bottom-right corner on the screen instead:
        the panel is anchored there, and that is the edge the user placed.
        """
        if not self.expanded:
            return
        self.update_idletasks()
        need = self.winfo_reqheight() + 8
        cur_h = self.winfo_height()
        if need - cur_h <= 4 and cur_h - need <= 24:
            return

        sh, sw = self.winfo_screenheight(), self.winfo_screenwidth()
        # hold the bottom edge where it is, and keep the panel on the screen
        bottom = self.winfo_y() + cur_h
        x = min(max(self.winfo_x(), 0), max(0, sw - W))
        y = max(0, min(bottom - need, sh - need))
        self.geometry(f"{W}x{need}+{x}+{y}")

    # ------------------------------------------------------------ data
    def refresh(self, usage: list[Usage]):
        self.last_usage = usage
        t = totals(usage)

        # collapsed bar
        if hasattr(self, "lbl_bar_run") and self.lbl_bar_run.winfo_exists():
            self.lbl_bar_run.configure(text=human(t["spent"]))
            self.lbl_bar_total.configure(text=human(t["total"]))

        if not self.expanded:
            return

        # the header mirrors the collapsed bar: THIS RUN then TOTAL, figure only,
        # with the session count tucked under the run's
        self.lbl_run.configure(text=human(t["spent"]))
        self.lbl_total.configure(text=human(t["total"]))
        self.lbl_sessions.configure(text=f"{t['sessions']} sessions")
        make_bar(self.tot_bar, t, t["total"], 4, padx=0, surface=BG)

        by_name = {u.source: u for u in usage}
        # A source that is not installed is NOT an error worth showing: a user
        # with two tools should see two rows, not four rows of red text. Missing
        # sources stay visible in tokenfloats.log and in --detect, just not in the
        # panel. A source that is present but unreadable is a different story and
        # is still reported inline.
        for name, w in self.blocks.items():
            u = by_name.get(name)
            if u is None:
                w["box"].pack_forget()
                continue
            if not u.ok:
                if not u.installed:
                    w["box"].pack_forget()
                    continue
                if not w["box"].winfo_manager():
                    w["box"].pack(fill="x", pady=(0, 14))
                w["mark"].configure(bg="#f7768e")
                w["names"].configure(fg=FG)
                w["sess"].configure(text="unreadable")
                w["fig"].configure(text=u.error[:26], fg="#f7768e",
                                   font=("Segoe UI", 9))
                w["run"].configure(text="")
                for c in w["bar"].winfo_children():
                    c.destroy()
                continue
            if not w["box"].winfo_manager():
                w["box"].pack(fill="x", pady=(0, 14))
            w["mark"].configure(bg=ACCENT.get(name, FG_DIM))
            w["names"].configure(fg=FG)
            w["sess"].configure(text=f"{u.sessions} sessions")
            w["fig"].configure(text=human(u.total), fg=FG,
                               font=("Segoe UI", 13, "bold"))
            # Always render, even at 0: a uniform row rhythm reads better than
            # rows of ragged height where only some tools were used this run.
            w["run"].configure(text=f"run {human(u.run)}", fg=FG_FAINT)
            make_bar(w["bar"], u.buckets(), u.total, 3, padx=14, surface=BG)
        if getattr(self, "history", None) is not None:
            self.history.reload()
        self.lbl_stamp.configure(text=f"updated {time.strftime('%H:%M:%S')}")
        self._autosize()

    def summary(self) -> str:
        """Tray tooltip. Mirrors the panel's two figures; "run" no longer
        exists as a concept, so naming it here would misdescribe the number."""
        t = totals(self.last_usage) if self.last_usage else {}
        return f"this run {human(t.get('spent', 0))} · total {human(t.get('total', 0))}"


class TokenFloatS:
    def __init__(self, config: dict | None = None):
        self.panel: Panel | None = None
        self.icon = None
        self.stop = False
        self.visible = True
        self.config = config if config is not None else load_config()
        self.interval = poll_seconds(self.config)
        # Set on quit so the poll thread stops waiting out its interval at once,
        # rather than keeping the process alive for up to `interval` seconds.
        self._stop_event = threading.Event()

    def poll_once(self):
        usage = collect_all(self.config)
        spent = sum(u.run for u in usage)
        if self.icon:
            self.icon.icon = tray_image(spent)
            self.icon.title = f"TokenFloatS  {self.panel.summary() if self.panel else ''}"
        if self.panel and self.panel.winfo_exists():
            self.panel.refresh(usage)
        return usage

    def loop(self):
        while not self.stop:
            try:
                self.poll_once()
            except Exception as e:                      # noqa: BLE001
                # Not print(): the app is launched with pythonw, where stderr is
                # a handle with nothing attached, so a poll failure would be
                # invisible and the panel would silently freeze on stale numbers.
                _log(f"poll failed: {type(e).__name__}: {e}")
            # A single interruptible wait, so quitting never has to drain a
            # sleep loop and the wake-up costs nothing while idle.
            if self._stop_event.wait(self.interval):
                return

    def toggle_panel(self, *_):
        if self.panel and self.panel.winfo_exists():
            self.visible = not self.visible
            if self.visible:
                self.panel.deiconify()
                self.panel.lift()
                self.panel.focus_force()
            else:
                self.panel.withdraw()
            return
        self.panel = Panel(self.hide)
        self.poll_once()
        self.panel.deiconify()

    def hide(self):
        if self.panel and self.panel.winfo_exists():
            self.panel.withdraw()
        self.visible = False

    def quit(self, *_):
        _log("quit requested from the tray menu")
        self.stop = True
        self._stop_event.set()          # wake the poll thread out of its wait
        if self.icon:
            self.icon.stop()
        if self.panel and self.panel.winfo_exists():
            self.panel.destroy()
        finish_counter()
    release_single_instance()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="print a snapshot and exit")
    ap.add_argument("--detect", action="store_true",
                    help="list which source paths were found, then exit")
    ap.add_argument("--interval", type=int, metavar="SECONDS",
                    help="poll interval, overriding config.json (minimum "
                         f"{MIN_POLL_SECONDS})")
    args = ap.parse_args()

    cfg = load_config()
    if args.interval is not None:
        cfg = {**cfg, "poll_seconds": args.interval}

    if args.detect:
        found, missing = detect_all(cfg)
        print(f"{len(found)} of {len(COLLECTORS)} sources found\n")
        print("found:")
        for source, path in found.items():
            print(f"  {source:16} {path}")
        absent = [k for k in missing if not k.startswith("+also")]
        if absent:
            print("\nnot installed (hidden in the panel):")
            for source in absent:
                for p in missing[source]:
                    print(f"  {source:16} expected at {p}")
        extra = {k: v for k, v in missing.items() if k.startswith("+also")}
        if extra:
            print("\nalso present (using the first):")
            for source, paths in extra.items():
                for p in paths:
                    print(f"  {source.replace('+also ', ''):16} {p}")
        return

    if args.once:
        data = collect_all(cfg)
        t = totals(data)
        for u in data:
            flag = "ok" if u.ok else f"ERR {u.error}"
            print(f"{u.source:18} {flag}  fresh={u.fresh:>13,}  all={u.total:>15,}"
                  f"  sessions={u.sessions}")
        print(f"\nTOTAL {t['total']:,}   fresh {t['fresh']:,}")
        for k in BAR_ORDER:
            v = t[k]
            pct = v / t["total"] * 100 if t["total"] else 0
            print(f"  {k:12} {v:>15,}  {pct:5.1f}%")
        return

    install_excepthook()
    _log("start")
    _log(f"poll interval: {poll_seconds(cfg)}s")
    found, missing = detect_all(cfg)
    absent = sorted(k for k in missing if not k.startswith("+also"))
    _log(f"detected {len(found)} source(s); missing: {absent}")
    if not acquire_single_instance():
        # Another copy already owns the lock: surface its window and exit quietly.
        _log("another instance holds the lock - waking it instead of starting a second")
        wake_existing_instance()
        return
    atexit.register(release_single_instance)

    try:
        app = TokenFloatS(cfg)
    except Exception as e:                          # noqa: BLE001
        _log(f"failed to build the app: {type(e).__name__}: {e}")
        raise
    menu = pystray.Menu(
        pystray.MenuItem("Show panel", app.toggle_panel, default=True),
        pystray.MenuItem("Refresh", lambda: app.poll_once()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", app.quit),
    )
    app.icon = pystray.Icon("tokenfloats", tray_image(0), "TokenFloatS", menu)
    threading.Thread(target=app.icon.run, name="tray", daemon=True).start()
    try:
        app.toggle_panel()
        threading.Thread(target=app.loop, name="poll", daemon=True).start()
        app.panel.mainloop()
    except Exception as e:                          # noqa: BLE001
        _log(f"UNCAUGHT in main loop: {type(e).__name__}: {e}")
        raise
    finally:
        _log("exiting")
        try:
            app.icon.stop()
        except Exception:                           # noqa: BLE001
            pass
        finish_counter()
    release_single_instance()


if __name__ == "__main__":
    main()
