import json
import math
import os
import shutil
import sys
import threading
import time
from collections import deque
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

def _enable_bundled_asio(roots=None):
    """Pass 24, item 6 — the user's 「asioもrbの使って」 (use the
    DDJ-RB's real ASIO, not the WASAPI substitute). sounddevice
    0.5.6 has an official slot for this: when the environment
    variable SD_ENABLE_ASIO is set, it loads
    libportaudio64bit-asio.dll from its portaudio-binaries
    folder instead of the bundled DLL (whose build has no ASIO
    host — pass 23's smoke evidence). CI builds that DLL with
    vcpkg's portaudio[asio] and places it there; the Steinberg
    SDK is fetched inside CI only and never committed. The
    variable is set ONLY when the DLL is really present — with
    it set and the file missing, sounddevice's import would
    fail outright. Must run before `import sounddevice`.
    Returns True when ASIO was enabled."""
    if os.environ.get('SD_ENABLE_ASIO'):
        return True
    if roots is None:
        roots = []
        try:
            import _sounddevice_data
            roots.append(Path(next(iter(
                _sounddevice_data.__path__))) / 'portaudio-binaries')
        except Exception:
            pass
        meipass = getattr(sys, '_MEIPASS', None)
        if meipass:
            roots.append(Path(meipass) / '_sounddevice_data'
                         / 'portaudio-binaries')
    for folder in roots:
        if (Path(folder) / 'libportaudio64bit-asio.dll').exists():
            os.environ['SD_ENABLE_ASIO'] = '1'
            return True
    return False

_enable_bundled_asio()
import numpy as np
import sounddevice as sd
import soundfile as sf
from PySide6.QtCore import (Qt, QTimer, Signal, QObject, QEvent,
    QRectF, QPointF)
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QFontMetrics,
    QLinearGradient, QPainter, QPainterPath, QPen, QPolygonF, QRadialGradient)
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QSlider, QFileDialog, QComboBox, QMessageBox, QFrame, QProgressBar, QScrollArea,
    QSizePolicy)
from analysis import AnalysisCache
from engine import (Engine, Journal, RATE, GRID_CONFIDENT,
                    WAVE_WINDOW_SECONDS, apply_cue_anchor,
                    format_mix_clock, grid_offset_through,
                    frame_to_x, load_track,
                    mix_timer_auto_start, mix_timer_reset, mix_timer_start,
                    mix_timer_stop, mix_timer_update, new_mix_timer,
                    peak_span, scroll_window, seek_fraction,
                    window_columns)
from audio_config import (diagnose_ladder, plan_auto, rb_name_match,
                          resolve_output)
from midi_input import MidiController
import als_import
import i18n
import perf_als
from setlist import (SetlistFormatError, display_rows, display_summary,
                     live_statuses, load_setlist, normalize_track,
                     setlist_to_clip_tracks)
from setlist import _guess_artist_title

# ---------------------------------------------------------------------------
# UI palette — rekordbox-style dark theme (second pass, 2026-11-26).
# Ground truth is now the user's OWN rekordbox screenshot (PERFORMANCE,
# 2Deck Horizontal): near-black background, hairline borders, blue
# waveforms on both decks, grey jog rings with a blue inner line and a
# huge BPM readout in the centre, dark pad cells, striped browser rows.
# Where that screenshot and the earlier REKORDBOX_UI_ANALYSIS.md
# estimates disagree (background warmth, panel tone, deck-2 waveform
# colour), the screenshot wins. Features rekordbox has but MixCraft
# does not (FX units, sampler, LINK, ratings...) are omitted, not faked.
# The rugged amber-LCD identity survives in one place only: the set
# list's "queued" state colour, which is a fixed user decision.
# ---------------------------------------------------------------------------
THEME = {
    'bg': '#0a0a0c',           # window background (near-black, neutral)
    'panel': '#131418',        # deck / mixer / browser panel surface
    'panel_hi': '#1d1f26',     # raised surface (inputs, slider grooves)
    'border': '#23252b',       # hairline borders
    'border_hi': '#33363f',
    'text': '#edeef0',
    'text_sub': '#9aa0a8',
    'text_mute': '#6d7278',
    'deck_a': '#3a3d46',       # pass 15: deck colour coding retired —
    'deck_b': '#3a3d46',       # both decks wear the same neutral grey
    'display_bg': '#050506',   # numeric readout / waveform glass
    'wave_grid': '#17191d',
}
COLOR_BG = THEME['bg']
COLOR_PANEL = THEME['panel']
COLOR_PANEL_HI = THEME['panel_hi']
COLOR_BUTTON = THEME['panel_hi']
COLOR_BUTTON_HOVER = '#262932'
COLOR_BORDER = THEME['border']
COLOR_BORDER_HI = THEME['border_hi']
COLOR_TEXT = THEME['text']
COLOR_TEXT_SUB = THEME['text_sub']
COLOR_TEXT_MUTE = THEME['text_mute']
COLOR_DECK_A = THEME['deck_a']   # pass 15: retired deck accent (neutral grey)
COLOR_DECK_B = THEME['deck_b']   # pass 15: retired deck accent (neutral grey)
COLOR_FADER_FILL = '#565b63'     # pass 15: neutral channel-fader fill
COLOR_DISPLAY_BG = THEME['display_bg']
COLOR_DISPLAY_FG = '#edeef0'     # numeric readouts: white on dark glass
COLOR_DISPLAY_DIM = '#9aa0a8'    # secondary readouts
COLOR_WAVE_BLUE = '#2e9bff'      # waveform body (his rekordbox blue, both decks)
COLOR_WAVE_LIGHT = '#7fc4ff'     # waveform inner / peak light
COLOR_LCD_BG = COLOR_DISPLAY_BG
COLOR_LCD_FG = '#ffb000'      # legacy name; now only the set-list queued amber
COLOR_LCD_DIM = COLOR_DISPLAY_DIM
COLOR_STATE_PLAYED = '#6e6e78'   # set list: played track (grey, settled in the phone design)
COLOR_STATE_PLAYING = '#7dff6a'  # set list: now playing (green)
COLOR_STATE_NEXT = '#ff5347'     # set list: up next (red). Queued tracks stay amber.
COLOR_STATE_QUEUED = '#ffb000'   # set list: queued (amber, fixed user decision)
COLOR_METER = '#2ee66b'          # level meter green (waveform RGB mid colour)
# Pass 20 (user review fix list, 2026-11-27): a SLIGHT colour
# accent returns — on exactly five knobs: the MASTER knob and
# each deck's GAIN and FILTER. A muted, desaturated amber on the
# indicator line + value arc only (caps and sizes unchanged);
# every other knob keeps the neutral grey. This is a "the
# important knobs" accent, not deck colour coding (pass 15's
# retirement of A=blue / B=red stands).
COLOR_KNOB_ACCENT = '#cc9433'    # muted amber: MASTER / GAIN / FILTER
COLOR_WAVE_BG = COLOR_DISPLAY_BG
COLOR_WAVE_GRID = THEME['wave_grid']
# Waveform band colours (rekordbox RGB scheme, estimates — analysis doc §2)
COLOR_WAVEFORM_LOW = '#ff3838'
COLOR_WAVEFORM_MID = '#2ee66b'
COLOR_WAVEFORM_HIGH = '#2e9bff'
# ---------------------------------------------------------------------------
# Pass 14 (user direction, 2026-11-27: 「昔の液晶みたいな感じやっぱり
# してほしいよね」): the DISPLAY surfaces become old-LCD windows set
# into the dark rugged chassis — the pale grey-green glass of old
# calculators / MD-CD players / early CDJ screens, dark green-charcoal
# ink, and a faint unlit dot-matrix ghost grid across the glass. The
# dot font (pass 12) reads as LCD pixels on it. LCD surfaces: the deck
# info + waveform windows, the SET BPM dial centre, the level meter,
# the MIX TIMER readouts and the set-list rows window. Everything
# else — chassis, knobs, faders, pads, transport — stays hardware.
# Ink-on-glass contrast (WCAG): ink ≈ 7.9:1, ink-soft ≈ 5.6:1
# against the lightest glass stop; ink-dim ≈ 3:1 is de-emphasis
# only (played rows), by design.
COLOR_LCD_GLASS = '#a9b494'      # LCD glass, top edge
COLOR_LCD_GLASS_HI = '#b6c0a3'   # LCD glass, centre band (lightest)
COLOR_LCD_GLASS_LO = '#96a285'   # LCD glass, bottom edge
COLOR_LCD_INK = '#222b1c'        # LCD ink: primary text / waveform
COLOR_LCD_INK_SOFT = '#37422c'   # LCD ink: secondary text on glass
COLOR_LCD_INK_DIM = '#5e6b50'    # LCD ink: de-emphasised (played)
COLOR_LCD_PALE = '#b6c0a3'       # glass-pale text on an inverted row
COLOR_LCD_PALE_DIM = '#8f997f'   # pale secondary on an inverted row
# Pass 15 (user direction, 2026-11-27: 「デッキの色分けいらねかも
# 今なってるのと今モニターしてるのわかるようにしてほしいな」): the
# deck A=blue / B=red colour coding is GONE — the A/B letters carry
# the identity, and two STATE markers carry the meaning instead:
# ON AIR (a FILLED ink chip + the heavy LCD frame) for the deck
# actually playing, and MONITOR (a HOLLOW ink chip) for the deck
# selected for monitoring. Filled vs hollow keeps the two states
# unmistakable even in monochrome. The constants above keep the old
# names so nothing else breaks; they no longer paint deck colours.
LCD_GLASS_GRADIENT = ('qlineargradient(x1:0, y1:0, x2:0, y2:1, '
                      'stop:0 #a9b494, stop:0.5 #b6c0a3, stop:1 #96a285)')
FONT_STACK = '"DotGothic16", "Segoe UI", "Yu Gothic UI", "Meiryo UI", sans-serif'
MONO_STACK = '"DotGothic16", Consolas, "DejaVu Sans Mono", "Courier New", monospace'
CONSOLE_FAMILIES = ['DotGothic16', 'Consolas', 'DejaVu Sans Mono',
                    'Courier New', 'monospace']

# ---------------------------------------------------------------------------
# Bundled dot font (pass 12, user direction 2026-11-27: 「文字全体を
# 変えたいんだ。そういうフォントに」). Pass 11 dotted only the numeric
# readouts with a hand-drawn 5x7 table; the user wants the WHOLE window
# in that letterform, Japanese included — so the app now bundles
# DotGothic16 (Fontworks, SIL Open Font License 1.1; see
# assets/fonts/README.md and OFL.txt), registers it at startup and
# puts it first in every font stack. If the file cannot be found or
# loaded, the stacks fall through to the previous system fonts and
# the app still runs — the look degrades, nothing breaks.
# ---------------------------------------------------------------------------
DOT_FONT_FAMILY = 'DotGothic16'
DOT_FONT_FILE = 'DotGothic16-Regular.ttf'
_dot_font_loaded = False


def dot_font_path():
    """Locate the bundled DotGothic16 TTF across run layouts:
    beside app.py in a repo checkout, inside the PyInstaller bundle
    (sys._MEIPASS, fed by build.py's --add-data), or in an assets
    folder beside the frozen executable. None when not found."""
    rel = Path('assets') / 'fonts' / DOT_FONT_FILE
    candidates = [Path(__file__).resolve().parent / rel]
    meipass = getattr(sys, '_MEIPASS', None)
    if meipass:
        candidates.append(Path(meipass) / rel)
    candidates.append(Path(sys.executable).resolve().parent / rel)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def load_dot_font():
    """Register the bundled dot font with Qt (idempotent). True when
    the DotGothic16 family is available afterwards."""
    global _dot_font_loaded
    if _dot_font_loaded:
        return True
    path = dot_font_path()
    if path is not None:
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id >= 0 and DOT_FONT_FAMILY in \
                QFontDatabase.applicationFontFamilies(font_id):
            _dot_font_loaded = True
    return _dot_font_loaded


def console_font(pixel_size=None, point_size=None, bold=False):
    """A QFont in the console letterform: DotGothic16 first, the old
    mono stack as fallback for systems where the bundle is missing."""
    font = QFont()
    font.setFamilies(CONSOLE_FAMILIES)
    if pixel_size is not None:
        font.setPixelSize(pixel_size)
    if point_size is not None:
        font.setPointSize(point_size)
    font.setBold(bold)
    return font
SECTION_LABEL_QSS = f'color:{COLOR_TEXT_MUTE};font-family:{MONO_STACK};font-size:9px;font-weight:700;letter-spacing:2px'
LCD_LABEL_QSS = f'color:{COLOR_LCD_INK_SOFT};font-family:{MONO_STACK};font-size:9px;font-weight:700;letter-spacing:2px'
LCD_GRADIENT = ('qlineargradient(x1:0, y1:0, x2:0, y2:1, '
                'stop:0 #0a0a0c, stop:0.45 #050506, stop:1 #070708)')

def lcd_qss(size, bold=True, fg=None):
    """Dark-glass numeric readout style for display-only labels."""
    return (f'background:{LCD_GRADIENT};color:{fg or COLOR_DISPLAY_FG};'
            f'font-family:{MONO_STACK};font-size:{size}px;letter-spacing:1px;'
            f'font-weight:{700 if bold else 400};'
            f'border:1px solid {COLOR_BORDER};border-radius:3px;padding:3px 10px')

# ---------------------------------------------------------------------------
# Old-LCD glass painting (pass 14), shared by every LCD surface: the
# Waveform, DotReadout, JogDial centre, the segment meter and the
# LcdWindow frames. One helper so all the windows share one glass.
# ---------------------------------------------------------------------------
def _lcd_gradient(top, bottom):
    glass = QLinearGradient(0, top, 0, bottom)
    glass.setColorAt(0.0, QColor(COLOR_LCD_GLASS))
    glass.setColorAt(0.5, QColor(COLOR_LCD_GLASS_HI))
    glass.setColorAt(1.0, QColor(COLOR_LCD_GLASS_LO))
    return glass


def _paint_lcd_overlay(p, rect):
    """The unlit dot-matrix ghost grid and the inset shading over an
    LCD glass surface: the grid is deliberately faint (alpha 13) —
    visible on a close look, never noisy behind text — and the dark
    top/left shading with the faint light bottom edge is what makes
    the surface read as a window SET INTO the chassis."""
    points = []
    x = rect.left() + 3
    while x < rect.right() - 1:
        y = rect.top() + 3
        while y < rect.bottom() - 1:
            points.append(QPointF(x, y))
            y += 6
        x += 6
    p.setPen(QPen(QColor(34, 43, 28, 13), 2, Qt.PenStyle.SolidLine,
                  Qt.PenCapStyle.RoundCap))
    p.drawPoints(QPolygonF(points))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(18, 24, 14, 70))
    p.drawRect(rect.left(), rect.top(), rect.width(), 2)
    p.setBrush(QColor(18, 24, 14, 36))
    p.drawRect(rect.left(), rect.top() + 2, rect.width(), 1)
    p.drawRect(rect.left(), rect.top(), 2, rect.height())
    p.setBrush(QColor(255, 255, 255, 28))
    p.drawRect(rect.left(), rect.bottom() - 1, rect.width(), 1)


def paint_lcd_glass(p, rect, bezel=True):
    """Fill a rect with old-LCD glass and finish it with the ghost
    grid + inset shading; the dark bezel line (optional) sets the
    window into the chassis."""
    p.fillRect(rect, _lcd_gradient(rect.top(), rect.bottom()))
    _paint_lcd_overlay(p, rect)
    if bezel:
        p.setPen(QPen(QColor('#131709'), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(rect.adjusted(0, 0, -1, -1))

def _is_bar_tick(k, bar_offset, beats_per_bar):
    """Pass 28: grid beat k is a BAR (downbeat) tick when
    (k - bar_offset) is a whole number of bars. The analysis
    fixes the beat phase only; bar_offset (0..3, the deck's
    stepper) is the user's downbeat correction, and both
    waveform views draw their strong ticks from this."""
    return (k - bar_offset) % beats_per_bar == 0


class Result(QObject):
    ready = Signal(int, object, str)

# Height of the thin full-track overview strip under the scrolling
# waveform (pass 7). Pass 9 slims both the widget and the strip a
# little — part of the height the browser panel gains.
WAVE_OVERVIEW_HEIGHT = 14

class Waveform(QWidget):
    """Pass 7 (user direction, 2026-11-26: 「波形データが流れるのも
    だいじ」): the main area is a SCROLLING waveform, rekordbox-style —
    a zoomed WAVE_WINDOW_SECONDS window flows past a playhead fixed at
    the centre as the engine position advances. Under it, a thin
    full-track overview strip keeps the whole song (and the position
    marker) in sight. Both areas draw the same track.peaks data.
    Window/position math lives in engine.py (scroll_window /
    peak_span / seek_fraction), Qt-free and unit-tested. Clicking
    the overview seeks by track fraction; clicking the scrolling
    area seeks to the time shown at that x.

    Pass 14: the widget is an old-LCD window — pale glass, ink peaks
    (see paintEvent). The constructor's `color` argument (the old
    rekordbox blue) is kept for the call sites' sake but no longer
    paints anything."""
    seek = Signal(float)
    def __init__(self, color):
        super().__init__()
        self.track = None
        self.position = 0
        self.playing = False   # pass 15: heavy ink frame while playing
        self.color = color
        # Pass 9 (sketch layout): the stacked strips give the flowing
        # waveform real height again — the user: 「波形データ大きく
        # てもいいよ」. 100 total (scrolling area + 14 overview).
        # Pass 24: the minimum gives a little back (84) so a short
        # screen compresses the waveform before it scrolls — the
        # design height still gets its full size from the stretch.
        self.setMinimumHeight(84)
    def paintEvent(self, event):
        p = QPainter(self)
        # Pass 14: the waveform lives behind old-LCD glass — pale
        # grey-green with the ghost dot grid (paint_lcd_glass), and
        # the peaks are drawn in INK, like an old MD/CD player's
        # display: what has played is full ink, what is ahead is
        # lighter ghost ink. (Pass 9's overview dimmed the played
        # part instead; the LCD density rule is the brief's, and it
        # applies to both areas alike.)
        paint_lcd_glass(p, self.rect())
        if self.playing:
            # Pass 15: the playing deck's waveform window wears the
            # same heavy ink frame as its info window.
            p.setPen(QPen(QColor(COLOR_LCD_INK), 3))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(self.rect().adjusted(1, 1, -2, -2))
        w, h = self.width(), self.height()
        main_h = h - WAVE_OVERVIEW_HEIGHT - 1
        mid = main_h/2
        if not self.track:
            p.setPen(QColor(COLOR_LCD_INK_DIM))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, i18n.tr('drop_hint'))
            return
        total = len(self.track.audio)
        start, end = scroll_window(self.position)
        # One-second grid lines scroll WITH the music, so the flow is
        # visible even in a steady passage. On the glass they are
        # faint ink rulings.
        p.setPen(QColor(34, 43, 28, 26))
        for s in range(math.ceil(start/RATE), int(end//RATE)+1):
            x = int(frame_to_x(s*RATE, start, end, w))
            p.drawLine(x, 0, x, main_h)
        p.setPen(QColor(34, 43, 28, 48))
        p.drawLine(0, int(mid), w, int(mid))
        # Pass 26: beat grid ticks — thin ink lines at the track's
        # beat positions (bar starts slightly stronger), drawn
        # UNDER the peaks so they read as rulings, not data. Only
        # when the grid is confident: a guessed grid drawn as fact
        # would be worse than none. The peaks' resolution and
        # drawing are untouched (the pass-23/24 rule stands).
        grid = self.track.grid()
        # A rekordbox-anchored grid draws too, at its anchored
        # phase — the same offset the engine aligns to.
        show_grid = grid is not None \
            and (self.track.beat_confidence >= GRID_CONFIDENT
                 or self.track.anchor_cue is not None)
        # Pass 30: the None case must not touch grid at all (the
        # condition was inverted — a beatless / unanalysed track,
        # whose grid() is None, crashed the paint with an
        # AttributeError storm). tick_offset is only read under
        # show_grid, which already requires a grid.
        tick_offset = 0 if grid is None else (
            grid_offset_through(grid, self.track.anchor_cue)
            if self.track.anchor_cue is not None else grid.offset)
        if show_grid:
            beat = grid.beat_frames
            # Pass 28: the BAR ticks honour the track's bar
            # offset — beat k is a bar start when (k - bar_offset)
            # is a whole number of bars. Analysis fixes the beat
            # phase only; the downbeat is the user's correction.
            bar_off = self.track.bar_offset if self.track else 0
            k_first = math.ceil((start - tick_offset) / beat)
            k_last = int((end - tick_offset) / beat)
            for k in range(k_first, k_last + 1):
                x = int(frame_to_x(tick_offset + k * beat,
                                   start, end, w))
                p.setPen(QColor(34, 43, 28,
                                85 if _is_bar_tick(k, bar_off,
                                                   grid.beats_per_bar)
                                else 36))
                p.drawLine(x, 0, x, main_h)
        # Pass 24 (user report 2026-11-27: 「波形がまだガタガタ」):
        # one 1px line per PIXEL COLUMN, each column carrying the
        # true max of the peak buckets inside it (engine.window_
        # columns over the pass-24 fine peaks, ~188 buckets/s).
        # The old drawing stretched each of the ≤1400 whole-track
        # buckets across ~10px of pen — the staircase he saw. The
        # playhead sits at the centre: columns at or left of it
        # have played (full ink), the rest are ahead (ghost); each
        # side is one pass so the pen is set only twice.
        cols = window_columns(self.track.peaks, total, start, end, w)
        mid_i = int(mid)
        p.setPen(QPen(QColor(COLOR_LCD_INK), 1))
        for x in range(0, w//2 + 1):
            y = int(cols[x]*(mid-6))
            p.drawLine(x, mid_i-y, x, mid_i+y)
        p.setPen(QPen(QColor(34, 43, 28, 100), 1))
        for x in range(w//2 + 1, w):
            y = int(cols[x]*(mid-6))
            p.drawLine(x, mid_i-y, x, mid_i+y)
        # Playhead: fixed at the centre of the scrolling area — a
        # solid ink line on the glass.
        p.setPen(QPen(QColor(COLOR_LCD_INK), 2))
        p.drawLine(w//2, 0, w//2, main_h)
        # Overview strip: the whole track, thin, with the position
        # marker — the scrolling area's map, in the same ink
        # densities (played full, ahead ghost). Pass 24: also
        # per-column true maxes — the old every-Nth-bucket sampling
        # let transients fall between the drawn columns.
        oy = main_h + 1
        p.setPen(QColor(34, 43, 28, 70))
        p.drawLine(0, main_h, w, main_h)
        omid = oy + WAVE_OVERVIEW_HEIGHT/2
        pos_x = int(self.position/max(1, total)*w)
        ocols = window_columns(self.track.peaks, total, 0, total, w)
        omid_i = int(omid)
        p.setPen(QPen(QColor(COLOR_LCD_INK), 1))
        for x in range(0, min(pos_x, w)):
            y = max(1, int(ocols[x]*(WAVE_OVERVIEW_HEIGHT/2-2)))
            p.drawLine(x, omid_i-y, x, omid_i+y)
        p.setPen(QPen(QColor(34, 43, 28, 100), 1))
        for x in range(min(pos_x, w), w):
            y = max(1, int(ocols[x]*(WAVE_OVERVIEW_HEIGHT/2-2)))
            p.drawLine(x, omid_i-y, x, omid_i+y)
        if show_grid:
            # Overview: BAR ticks only — beat ticks would merge
            # into a solid band at whole-track scale. Pass 28:
            # the bars start at the bar-offset downbeat, not at
            # the analysed beat phase.
            bar = grid.bar_frames
            bar_origin = tick_offset + bar_off * beat
            k_first = math.ceil((0 - bar_origin) / bar)
            k_last = int((total - bar_origin) / bar)
            p.setPen(QColor(34, 43, 28, 70))
            for k in range(k_first, k_last + 1):
                x = int((bar_origin + k * bar) / max(1, total) * w)
                p.drawLine(x, oy, x, oy + WAVE_OVERVIEW_HEIGHT)
        p.setPen(QPen(QColor(COLOR_LCD_INK), 2))
        p.drawLine(pos_x, oy, pos_x, oy + WAVE_OVERVIEW_HEIGHT)
    def mousePressEvent(self, event):
        if self.track:
            pos = event.position()
            self.seek.emit(seek_fraction(pos.x(), pos.y(), self.width(),
                                         self.height(), WAVE_OVERVIEW_HEIGHT,
                                         self.position, len(self.track.audio)))

class JogDial(QWidget):
    """Circular BPM dial echoing the rekordbox jog ring: grey ring,
    thin blue inner line, red position marker, huge BPM in the
    centre with the pitch % and range under it.

    Pass 5 (user direction, 2026-11-26): both decks run at the set
    BPM, so there is ONE dial, in the centre mixer column, showing
    the SET BPM. With a caption ('SET BPM') the rotating position
    marker and the pitch % / range lines are replaced by the
    caption — a set tempo has no platter position or pitch range.

    Pass 18 audit: the dial does NOT share the Knob's clipping —
    its painting is inset 5px from the widget edge while its
    widest pen overhang is ~1px, so the ring clears every edge.

    Pass 26: the SET BPM dial takes input — drag vertically or
    scroll to move the set tempo, reported through on_change (the
    window clamps and quantizes). With on_change unset the dial
    stays display-only, as before."""
    DRAG_BPM_PER_PX = 0.05     # 20 px of drag per BPM
    def __init__(self, caption=None, size=118):
        super().__init__()
        self.bpm = None
        self.fraction = 0.0
        self.caption = caption
        self.on_change = None
        self._drag = None      # (start_y, start_bpm) while dragging
        self.setFixedSize(size, size)
    def set_bpm(self, bpm):
        self.bpm = bpm
        self.update()
    def mousePressEvent(self, event):
        if self.on_change is not None and self.bpm:
            self._drag = (event.position().y(), self.bpm)
            event.accept()
    def mouseMoveEvent(self, event):
        if self._drag is not None:
            start_y, start_bpm = self._drag
            self.on_change(start_bpm
                           + (start_y - event.position().y())
                           * self.DRAG_BPM_PER_PX)
            event.accept()
    def mouseReleaseEvent(self, event):
        self._drag = None
    def wheelEvent(self, event):
        if self.on_change is not None and self.bpm:
            steps = event.angleDelta().y() / 120.0
            if steps:
                steps = int(steps) or (1 if steps > 0 else -1)
                self.on_change(self.bpm + 0.1 * steps)
                event.accept()
    def set_caption(self, caption):
        """Language registry hook (pass 10): the painted caption
        ('SET BPM' / 'セットBPM') is redrawn in the new language."""
        if caption != self.caption:
            self.caption = caption
            self.update()
    def set_fraction(self, fraction):
        self.fraction = fraction % 1.0
        self.update()
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(5, 5, -5, -5)
        # Pass 9: machined face — radial sheen from the upper left,
        # a brighter rim light on the outer ring.
        face = QRadialGradient(rect.center().x() - rect.width()*0.25,
                               rect.center().y() - rect.height()*0.25,
                               rect.width()*0.75)
        face.setColorAt(0.0, QColor('#1d1e24'))
        face.setColorAt(0.55, QColor('#101114'))
        face.setColorAt(1.0, QColor('#08080a'))
        p.setPen(QPen(QColor('#3d404a'), 2))
        p.setBrush(face)
        p.drawEllipse(rect)
        inner = rect.adjusted(8, 8, -8, -8)
        p.setPen(QPen(QColor('#10130c'), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(inner)
        # Pass 14: the dial's display centre is an old-LCD window —
        # pale glass and ink digits inside the chassis bezel ring.
        # The glass is clipped to the disc; its ghost grid and the
        # top shading chord read as the LCD's own shading.
        disc = inner.adjusted(3, 3, -3, -3)
        clip = QPainterPath()
        clip.addEllipse(QRectF(disc))
        p.save()
        p.setClipPath(clip)
        p.fillRect(disc, _lcd_gradient(disc.top(), disc.bottom()))
        _paint_lcd_overlay(p, disc)
        p.restore()
        if self.caption is None:
            cx, cy = rect.center().x(), rect.center().y()
            radius = rect.width()/2 - 3
            angle = -math.pi/2 + self.fraction*2*math.pi
            mx, my = cx + radius*math.cos(angle), cy + radius*math.sin(angle)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(COLOR_LCD_INK))
            p.drawEllipse(int(mx)-3, int(my)-3, 6, 6)
        bpm_text = f'{self.bpm:.2f}' if self.bpm else '--.--'
        # Pass 12: the dial's number is drawn in the bundled
        # DotGothic16 — the same letterform as the whole window —
        # sized to fit the dial's inner width, never clipped.
        number_font = console_font(pixel_size=36)
        advance = QFontMetrics(number_font).horizontalAdvance(bpm_text)
        max_width = rect.width() - 34
        if advance > max_width:
            number_font.setPixelSize(
                max(10, int(36 * max_width / advance)))
        p.setFont(number_font)
        p.setPen(QColor(COLOR_LCD_INK))
        p.drawText(rect.adjusted(0, -12, 0, -12),
                   Qt.AlignmentFlag.AlignCenter, bpm_text)
        mono = console_font(point_size=9)
        p.setFont(mono)
        if self.caption is not None:
            p.setPen(QColor(COLOR_LCD_INK_SOFT))
            p.drawText(inner.adjusted(0, 22, 0, 0), Qt.AlignmentFlag.AlignCenter, self.caption)
            return
        p.setPen(QColor(COLOR_LCD_INK_SOFT))
        p.drawText(inner.adjusted(0, 22, 0, 0), Qt.AlignmentFlag.AlignCenter, '0.0%')
        p.setPen(QColor(COLOR_LCD_INK))
        p.drawText(inner.adjusted(0, 40, 0, 0), Qt.AlignmentFlag.AlignCenter, '±6')

class MeterLamp(QWidget):
    """Level meter. Pass 9 made it two round lamps (the two small
    circles in the user's layout sketch); pass 14 (old-LCD
    direction) makes it a SEGMENT BAR behind LCD glass: eight
    segment blocks, lit = solid ink, unlit = ghost ink, and the
    last block — the near-limiter segment — wears a marker (a pale
    notch cut into it, with a tick above) when it lights. Same
    contract Window.tick() used on the old QProgressBar —
    setRange / setValue(int) / setToolTip — driven by the engine's
    single peak value. The class name stays: the wiring names it."""
    SEGMENTS = 8
    def __init__(self):
        super().__init__()
        self._value = 0
        self.setFixedSize(76, 32)
    def setRange(self, minimum, maximum):
        pass  # the segments are threshold-based; the range is not used
    def setValue(self, value):
        value = int(value)
        if value != self._value:
            self._value = value
            self.update()
    def lit_segments(self):
        """Segments lit at the current value: any real signal (the
        old lamp 1's >4 threshold) lights the first block, then the
        bar fills proportionally to the peak."""
        if self._value <= 4:
            return 0
        return min(self.SEGMENTS,
                   max(1, round(self._value * self.SEGMENTS / 100)))
    def paintEvent(self, event):
        p = QPainter(self)
        paint_lcd_glass(p, self.rect())
        lit = self.lit_segments()
        gap = 2
        left, top = 6, 11
        seg_w = (self.width() - 2 * left
                 - gap * (self.SEGMENTS - 1)) / self.SEGMENTS
        seg_h = self.height() - top - 6
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(self.SEGMENTS):
            x = left + i * (seg_w + gap)
            if i < lit:
                p.setBrush(QColor(COLOR_LCD_INK))
            else:
                p.setBrush(QColor(34, 43, 28, 30))
            p.drawRect(QRectF(x, top, seg_w, seg_h))
            if i == self.SEGMENTS - 1 and i < lit:
                # Near-limiter marker: a pale notch cut into the
                # last block, and a small ink tick above it.
                p.setBrush(QColor(COLOR_LCD_GLASS_HI))
                p.drawRect(QRectF(x + seg_w / 2 - 1, top + 2, 2, 4))
                cx = x + seg_w / 2
                p.setBrush(QColor(COLOR_LCD_INK))
                p.drawPolygon(QPolygonF([QPointF(cx - 3, 3),
                                         QPointF(cx + 3, 3),
                                         QPointF(cx, 7)]))

class DotReadout(QWidget):
    """Console readout on dark glass. Pass 11 painted the digits as
    hand-drawn 5x7 dot grids (the retired dotfont module) and left
    kana/kanji in a system font; pass 12 (user direction 2026-11-27:
    「文字全体を変えたいんだ。そういうフォントに」) draws the WHOLE
    string — Japanese included — in the bundled DotGothic16, the
    same letterform as the rest of the window. Pass 14: the glass
    itself is old-LCD now (pale grey-green, ghost dot grid, ink
    text — paint_lcd_glass), so the readouts are small LCD windows
    in the chassis. setText() / text() mirror the QLabel API the
    readouts had, so the tick() / loaded() wiring is unchanged.
    `pitch` keeps the pass-11 call sites' sizing: it scales the
    font's pixel size, and if the text would overflow the glass
    the size scales down to fit — never clipped."""
    def __init__(self, pitch=2.0, color=COLOR_LCD_INK, align='left',
                 height=28):
        super().__init__()
        self._text = ''
        self.pitch = float(pitch)
        self.color = QColor(color)
        self.align = align
        self.setFixedHeight(height)
    def setText(self, text):
        if text != self._text:
            self._text = text
            self.update()
    def text(self):
        return self._text
    def _pixel_size(self):
        # The pass-11 dot runs stood GLYPH_H(7) * pitch px tall;
        # DotGothic16's ink nearly fills its em, so a slightly
        # larger pixel size keeps the same visual weight.
        return max(10, int(round(self.pitch * 10.5)))
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint_lcd_glass(p, self.rect())
        if not self._text:
            return
        font = console_font(pixel_size=self._pixel_size(), bold=True)
        advance = QFontMetrics(font).horizontalAdvance(self._text)
        if advance > self.width() - 8:
            font.setPixelSize(max(
                8, int(self._pixel_size() * (self.width() - 8) / advance)))
        p.setFont(font)
        p.setPen(self.color)
        flags = Qt.AlignmentFlag.AlignVCenter
        flags |= (Qt.AlignmentFlag.AlignCenter if self.align == 'center'
                  else Qt.AlignmentFlag.AlignLeft)
        p.drawText(self.rect().adjusted(4, 0, -4, 0), flags, self._text)


def _paint_screws(p, width, height):
    """Pass 11 rugged material: small corner screws over a panel's
    styled surface — rack-gear hardware, not a glossy card."""
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    r, inset = 2.6, 7
    for cx, cy in ((inset, inset), (width - inset, inset),
                   (inset, height - inset), (width - inset, height - inset)):
        p.setPen(QPen(QColor('#3a3d45'), 1))
        p.setBrush(QColor('#1d1e23'))
        p.drawEllipse(QRectF(cx - r, cy - r, 2 * r, 2 * r))
        p.setPen(QPen(QColor('#08080a'), 1.1))
        p.drawLine(int(cx - r + 0.7), int(cy), int(cx + r - 0.7), int(cy))


class PanelFrame(QFrame):
    """A QFrame that adds the pass-11 corner screws over its styled
    surface. The stylesheet still paints first (super), the screws
    are drawn on top."""
    def paintEvent(self, event):
        super().paintEvent(event)
        _paint_screws(QPainter(self), self.width(), self.height())


class SetlistDropPanel(PanelFrame):
    """The set-list panel (pass 24, user report 2026-11-27: 「セッ
    トリストにドラッグしても音楽は入らない」): dropping audio
    files anywhere on the panel adds them to the set list. The
    panel's children (the LCD list, the rows, their labels) do
    not accept drops, so Qt propagates the drag/drop events up
    to this frame — the same mechanism the decks' drop loading
    relies on. `drop_handler` is the callable the Window
    installs; it receives the dropped local file paths.

    Pass 25 (user report, 2026-11-28: 「トラックリストにドラッ
    クドロップできない」): propagation alone did not take on
    his machine, so the Window now ALSO arms every descendant
    with a SetlistDropFilter (below) — the drop is caught on
    whichever child sits under the cursor — and the panel wears
    a 'drop_active' highlight while a drag hovers, so the live
    target is visible. These own handlers stay as the backstop
    for drops landing on the frame itself."""
    drop_handler = None
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls()
                 if u.isLocalFile()]
        if self.drop_handler is not None:
            self.drop_handler(paths)
            event.acceptProposedAction()
    def set_drop_highlight(self, on):
        """The hover highlight (pass 25): an amber frame (the
        STYLE rule for [drop_active="true"]) while a file drag
        is over the panel, so he can SEE the target is live."""
        on = bool(on)
        if self.property('drop_active') == on:
            return
        self.setProperty('drop_active', on)
        self.style().unpolish(self)
        self.style().polish(self)


class SetlistDropFilter(QObject):
    """Event filter arming the set-list panel AND every child
    (pass 25): drag enter/move/drop are intercepted on whichever
    descendant is under the cursor, not left to Qt's propagation
    (which pass 24 relied on and his machine did not honour).
    The Window installs it after building the panel and again
    after every render_setlist (rows are rebuilt there). The
    handler receives the dropped local paths — possibly empty,
    so a URL-only drop still earns a status explanation."""
    def __init__(self, panel, handler):
        super().__init__(panel)
        self.panel = panel
        self.handler = handler
    def eventFilter(self, obj, event):
        kind = event.type()
        if kind in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
            if event.mimeData().hasUrls():
                event.acceptProposedAction()
                self.panel.set_drop_highlight(True)
                return True
        elif kind == QEvent.Type.DragLeave:
            self.panel.set_drop_highlight(False)
        elif kind == QEvent.Type.Drop:
            self.panel.set_drop_highlight(False)
            if event.mimeData().hasUrls():
                paths = [u.toLocalFile()
                         for u in event.mimeData().urls()
                         if u.isLocalFile()]
                self.handler(paths)
                event.acceptProposedAction()
                return True
        return False


class SetlistRow(QFrame):
    """One set-list row (pass 24): a double-click loads the
    entry's file onto the standby deck (Window.load_setlist_
    entry). The row's labels do not handle the click, so it
    propagates up to this frame.

    Pass 32: a single click SELECTS the row — it moves the
    browse cursor (the same selection the DDJ-RB's BROWSE knob
    walks and its LOAD buttons load from), so mouse and
    hardware share one notion of 'the selected row', the way
    rekordbox's browser does."""
    def __init__(self, index, on_activate, on_select=None):
        super().__init__()
        self.entry_index = index
        self._on_activate = on_activate
        self._on_select = on_select
    def mousePressEvent(self, event):
        if self._on_select is not None:
            self._on_select(self.entry_index)
        super().mousePressEvent(event)
    def mouseDoubleClickEvent(self, event):
        self._on_activate(self.entry_index)
        super().mouseDoubleClickEvent(event)


class LcdWindow(QFrame):
    """An old-LCD display window set into the chassis (pass 14):
    the stylesheet paints the pale glass and the bezel, then the
    ghost dot grid + inset shading are drawn over it — the children
    paint above both, so their text sits ON the glass. Used for the
    deck info block and the set-list rows area. No screws: a display
    window is glass, not a screwed-down panel."""
    def set_heavy_frame(self, heavy):
        """Pass 15: the PLAYING deck's info window wears a heavier
        ink frame (the STYLE rule for QFrame#lcdinfo[heavy="true"])
        — one of the two state signals that replaced deck colours."""
        heavy = bool(heavy)
        if self.property('heavy') == heavy:
            return
        self.setProperty('heavy', heavy)
        self.style().unpolish(self)
        self.style().polish(self)
    def paintEvent(self, event):
        super().paintEvent(event)
        _paint_lcd_overlay(QPainter(self), self.rect())


class Knob(QWidget):
    """Rotary mixer knob (user direction, 2026-11-26: the EQ / GAIN /
    FILTER parameters read far better as knobs like a normal DJ mixer
    than as horizontal sliders). It keeps the exact contract of the
    QSlider it replaces — integer value in a set range and a
    valueChanged(int) signal — so the Deck's engine wiring (commands,
    ranges, defaults) is untouched; only the widget changed.
    Interaction: vertical mouse drag, mouse wheel, arrow keys.
    Bipolar controls (EQ bands, FILTER) draw the value arc from the
    centre detent and snap softly onto it; GAIN is unipolar and its
    arc runs from minimum."""
    valueChanged = Signal(int)
    SWEEP = 135.0    # pointer travel: ±135° from top (270° total)
    DETENT = 3       # ±value units around centre that snap (bipolar)
    DIAMETER = 34    # pass 9: compact cells for the mixer block
    # Pass 18 geometry (user direction, 2026-11-27: 「ノブの周りの
    # 円が切れてるのきになるな」): the paint reach around the knob
    # centre is the arc radius (DIAMETER/2 + 5 = 22) plus the tick
    # overhang (+5) plus ~1px of pen = 28px. The old 64x52 cell put
    # the centre only 22px from the top, so the ring and its centre
    # tick were clipped by ~6px at the top (and the end ticks
    # grazed the label). The cell is now 64x64 with the centre 30px
    # down: the full ring + ticks clear the top by 2px, the lowest
    # tick reach (~50px) clears the label zone (52px), and the
    # sides keep their 4px. The cap diameter is unchanged — this
    # is room for the ring, not a bigger knob.
    WIDTH = 64
    HEIGHT = 64
    CENTRE_Y = 30

    def __init__(self, label, color, bipolar=True):
        super().__init__()
        self.label = label
        self.color = QColor(color)
        self.bipolar = bipolar
        self._min, self._max, self._value = 0, 100, 0
        self._drag_start = None
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def set_label(self, label):
        """Language registry hook (pass 10): the MASTER knob's painted
        caption switches language; the deck knobs keep their English
        hardware captions (see i18n.py's conventions note)."""
        if label != self.label:
            self.label = label
            self.update()

    # --- QSlider-compatible subset (Deck wiring and smoke.py) ---
    def setRange(self, minimum, maximum):
        self._min, self._max = minimum, maximum
        self.setValue(self._value)
    def setValue(self, value):
        value = max(self._min, min(self._max, int(round(value))))
        if value != self._value:
            self._value = value
            self.valueChanged.emit(value)
            self.update()
    def value(self):
        return self._value

    def _fraction(self):
        span = self._max - self._min
        return (self._value - self._min) / span if span else 0.0
    def _centre(self):
        return (self._min + self._max) / 2
    def _angle(self, fraction):
        """Pointer angle in degrees, clockwise from 12 o'clock."""
        return -self.SWEEP + fraction * 2 * self.SWEEP
    @staticmethod
    def _point(cx, cy, radius, angle_deg):
        a = math.radians(angle_deg)
        return cx + radius * math.sin(a), cy - radius * math.cos(a)
    def _snapped(self, value):
        if self.bipolar and abs(value - self._centre()) <= self.DETENT:
            return self._centre()
        return value

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = (event.position().y(), self._value)
            self.setFocus()
            event.accept()
    def mouseMoveEvent(self, event):
        if self._drag_start is not None:
            start_y, start_value = self._drag_start
            span = self._max - self._min
            dragged = start_value + (start_y - event.position().y()) * span / 150.0
            self.setValue(self._snapped(dragged))
            event.accept()
    def mouseReleaseEvent(self, event):
        self._drag_start = None
        event.accept()
    def wheelEvent(self, event):
        steps = event.angleDelta().y() / 120.0
        if steps:
            self.setValue(self._snapped(self._value + steps * 4))
            event.accept()
    def keyPressEvent(self, event):
        step = {Qt.Key.Key_Up: 2, Qt.Key.Key_Right: 2,
                Qt.Key.Key_Down: -2, Qt.Key.Key_Left: -2}.get(event.key())
        if step is None:
            super().keyPressEvent(event)
        else:
            self.setValue(self._snapped(self._value + step))
            event.accept()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx = self.width() / 2
        cy = self.CENTRE_Y   # pass 18: see the geometry note above
        radius = self.DIAMETER / 2
        arc_r = radius + 5
        def arc(f_from, f_to, pen):
            p.setPen(pen)
            steps = max(2, int(abs(f_to - f_from) * 90))
            pts = [self._point(cx, cy, arc_r, self._angle(f_from + (f_to - f_from) * i / steps))
                   for i in range(steps + 1)]
            for a, b in zip(pts, pts[1:]):
                p.drawLine(int(a[0]), int(a[1]), int(b[0]), int(b[1]))
        # Full-sweep track, then the value arc in the knob colour
        # (pass 15: neutral grey everywhere; pass 20: MASTER /
        # GAIN / FILTER wear the muted-amber accent instead).
        arc(0.0, 1.0, QPen(QColor(COLOR_BORDER_HI), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        fraction = self._fraction()
        arc(0.5 if self.bipolar else 0.0, fraction,
            QPen(self.color, 3.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        # Min / max end ticks, plus a bright centre tick for bipolar.
        p.setPen(QPen(QColor(COLOR_TEXT_MUTE), 2))
        for frac in (0.0, 1.0):
            x1, y1 = self._point(cx, cy, arc_r - 4, self._angle(frac))
            x2, y2 = self._point(cx, cy, arc_r + 4, self._angle(frac))
            p.drawLine(int(x1), int(y1), int(x2), int(y2))
        if self.bipolar:
            x1, y1 = self._point(cx, cy, arc_r - 5, 0.0)
            x2, y2 = self._point(cx, cy, arc_r + 5, 0.0)
            p.setPen(QPen(QColor(COLOR_TEXT), 2))
            p.drawLine(int(x1), int(y1), int(x2), int(y2))
        # Knob cap (pass 9): radial shading lit from the upper left
        # like a machined metal cap; the indicator line wears the
        # knob colour (pass 20: amber on MASTER / GAIN / FILTER).
        cap = QRadialGradient(cx - radius * 0.4, cy - radius * 0.4, radius * 1.35)
        cap.setColorAt(0.0, QColor('#24252b'))
        cap.setColorAt(0.55, QColor('#15161a'))
        cap.setColorAt(1.0, QColor('#0c0c0f'))
        p.setPen(QPen(QColor('#3a3d46'), 1.2))
        p.setBrush(cap)
        p.drawEllipse(QRectF(cx - radius, cy - radius, self.DIAMETER, self.DIAMETER))
        angle = self._angle(fraction)
        x1, y1 = self._point(cx, cy, radius * 0.40, angle)
        x2, y2 = self._point(cx, cy, radius * 0.86, angle)
        p.setPen(QPen(self.color, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(int(x1), int(y1), int(x2), int(y2))
        # Label beneath the knob.
        p.setFont(console_font(point_size=8, bold=True))
        p.setPen(QColor(COLOR_TEXT_MUTE))
        p.drawText(QRectF(0, self.height() - 12, self.width(), 12), Qt.AlignmentFlag.AlignCenter, self.label)

def _path_matches(entry_path, target_path):
    """Set-list entry path vs a loaded track's path: exact match,
    or by file name when the entry carries a bare name. Shared by
    deck_title_text (pass 13) and the pass-15 monitor-row marker."""
    if not entry_path:
        return False
    candidate, target = Path(entry_path), Path(target_path)
    return candidate == target or (not candidate.is_absolute()
                                   and candidate.name == target.name)


# ---------------------------------------------------------------------------
# App settings (pass 24): a small JSON in the app data dir, beside
# the session journals. Today it remembers the Live-export base
# .als (so the picker is a once-ever question, not a per-export
# stall) and the folders the export dialogs last visited.
# ---------------------------------------------------------------------------

SETTINGS_FILENAME = 'settings.json'

def load_app_settings(data_dir):
    """The settings dict, or {} when absent/unreadable."""
    try:
        data = json.loads((Path(data_dir) / SETTINGS_FILENAME)
                          .read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def save_app_settings(data_dir, settings):
    """Best-effort persist; a failed save must never break a
    performance — the choice is simply asked again next time."""
    try:
        target = Path(data_dir) / SETTINGS_FILENAME
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(settings, ensure_ascii=False,
                                     indent=2), encoding='utf-8')
    except OSError:
        pass

def live_templates_dir(home=None):
    """Ableton's User Library Templates folder — where Live's
    'Save as Template' puts the base .als — or None when it does
    not exist on this machine."""
    home = Path(home) if home is not None else Path.home()
    candidate = home / 'Documents' / 'Ableton' / 'User Library' / 'Templates'
    return candidate if candidate.is_dir() else None

def live_base_start_dir(settings, home=None):
    """Where the base-.als picker opens (pass 24): Live's
    Templates folder when it exists, else the folder the picker
    last visited, else '' (the dialog's own default)."""
    templates = live_templates_dir(home)
    if templates is not None:
        return str(templates)
    last = (settings or {}).get('live_base_dir')
    if last and Path(last).is_dir():
        return last
    return ''

# ---------------------------------------------------------------------------
# Pass 25: audio diagnostics + the bundled Live base.
# ---------------------------------------------------------------------------

AUDIO_DIAG_FILENAME = 'audio-diagnostics.json'

def save_audio_diagnostics(data_dir, diag):
    """Best-effort write of the last audio ladder outcome beside
    the settings (pass 25): when the sound lands somewhere
    unexpected, this file says which devices were seen, which
    rung was picked and why each open failed — he can send it
    instead of describing a status bar. A failed write changes
    nothing."""
    try:
        target = Path(data_dir) / AUDIO_DIAG_FILENAME
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(diag, ensure_ascii=False,
                                     indent=2, default=str),
                          encoding='utf-8')
    except OSError:
        pass

# ---------------------------------------------------------------------------
# Pass 36: the diagnostics export. The pass-25 record above sat in
# %LOCALAPPDATA%\MixCraftDJStudio and the ASIO diagnosis stalled
# for hours on fetching it out of AppData by hand — so the app now
# carries the file to the Desktop itself, under a name he can
# find, and says what the record means in one line.
# ---------------------------------------------------------------------------

DIAG_DESKTOP_FILENAME = 'MixAble-audio-diagnostics.json'

def desktop_dir():
    """The user's Desktop — where the diagnostics export lands:
    a place he can find without digging through AppData."""
    return Path.home() / 'Desktop'

def read_audio_diagnostics(data_dir):
    """The last ladder record as a dict, or None when there is
    no readable file."""
    try:
        return json.loads(
            (Path(data_dir) / AUDIO_DIAG_FILENAME).read_text(
                encoding='utf-8'))
    except (OSError, ValueError):
        return None

def copy_audio_diagnostics(data_dir, dest_dir):
    """Copy audio-diagnostics.json into dest_dir under the
    findable Desktop name -> the destination Path, or None when
    there is nothing to copy or the copy failed."""
    source = Path(data_dir) / AUDIO_DIAG_FILENAME
    if not source.is_file():
        return None
    try:
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / DIAG_DESKTOP_FILENAME
        shutil.copyfile(source, dest)
        return dest
    except OSError:
        return None

def audio_diag_summary(diag, lang):
    """One plain line from a ladder record: what is making
    sound now, or why the DDJ-RB is not. Read from the record
    itself — never from live widget state — so the dialog tells
    the same story the exported file does."""
    if not isinstance(diag, dict):
        return i18n.tr('diag_outcome_unknown', lang)
    outcome = diag.get('outcome')
    attempts = diag.get('attempts') or []
    if outcome == 'opened':
        for record in attempts:
            if record.get('ok'):
                host = record.get('hostapi') or 'default host'
                if record.get('name'):
                    return i18n.tr('diag_outcome_opened', lang) \
                        .format(device=record['name'], host=host)
                return i18n.tr('diag_outcome_opened_default',
                               lang).format(host=host)
        return i18n.tr('diag_outcome_unknown', lang)
    if outcome == 'silent':
        return i18n.tr('diag_outcome_silent', lang)
    if outcome == 'system_default_fallback':
        rb_indices = {d.get('index')
                      for d in diag.get('rb_devices') or []}
        for record in attempts:
            if record.get('device') in rb_indices \
                    and record.get('error'):
                return i18n.tr('diag_outcome_rb_failed', lang) \
                    .format(error=record['error'])
        if not rb_indices:
            return i18n.tr('diag_outcome_rb_absent', lang)
        return i18n.tr('diag_outcome_fallback', lang)
    return i18n.tr('diag_outcome_unknown', lang)

BUNDLED_TEMPLATE_NAME = 'template.als'

def bundled_template_path(roots=None):
    """The user's own Live template, pre-placed (pass 25, his
    proposal: 「もう最初のファイルの中に組み込んでおいて」): a
    file named template.als beside the app — beside the frozen
    executable, or beside app.py in a checkout — is the export's
    base automatically, no picker, no settings. None when no
    such file exists."""
    if roots is None:
        roots = [Path(sys.executable).resolve().parent,
                 Path(__file__).resolve().parent]
    seen = set()
    for root in roots:
        candidate = Path(root) / BUNDLED_TEMPLATE_NAME
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.is_file():
            return candidate
    return None

# Audio extensions the deck load dialog accepts — a set-list
# drop accepts exactly the same files (pass 24).
SETLIST_AUDIO_EXTS = {'.wav', '.mp3', '.flac', '.aiff', '.aif'}

def file_track_entry(path):
    """A normalized set-list entry for a local audio file
    (pass 24, set-list drag & drop). Title / artist come from
    the file's embedded tags — the same soundfile read
    deck_title_text uses — falling back to the 'Artist - Title'
    filename guess (setlist._guess_artist_title, the preview
    tooling's rule); the duration is measured from the file
    header. BPM / key stay None on purpose: a drop must stay
    instant, and the deck's own analysis fills them in when
    the track is loaded."""
    path = Path(path)
    title = artist = None
    duration = None
    try:
        with sf.SoundFile(str(path)) as handle:
            title = (handle.title or '').strip() or None
            artist = (handle.artist or '').strip() or None
            if handle.samplerate:
                duration = int(round(handle.frames / handle.samplerate))
    except Exception:
        title = artist = duration = None
    if not title:
        guess_artist, guess_title = _guess_artist_title(path.name)
        title = guess_title or path.stem
        artist = artist or guess_artist
    return normalize_track({'title': title, 'artist': artist,
                            'duration_seconds': duration,
                            'path': str(path)})

def deck_title_text(path, setlist_data=None):
    """Header title for a loaded deck track (pass 13).

    'Title — Artist' from the metadata the app already knows, in
    order: (1) the loaded set list's entry for this file — matched
    on its `path` field (exactly, or by file name when the entry
    carries a bare name); (2) the file's embedded title / artist
    tags. Only when neither source knows a title does the raw file
    name (deck-letter prefix and all) show.
    """
    title = artist = None
    if setlist_data:
        for entry in setlist_data.get('tracks') or []:
            if _path_matches(entry.get('path'), path):
                title = entry.get('title') or None
                artist = entry.get('artist')
                break
    if not title:
        try:
            with sf.SoundFile(str(path)) as handle:
                title = (handle.title or '').strip() or None
                artist = (handle.artist or '').strip() or None
        except Exception:
            title = artist = None
    if title and artist:
        return f'{title} — {artist}'
    if title:
        return title
    return Path(path).name


class BarOffsetLabel(QLabel):
    """Pass 28: the bar-offset stepper's readout — focusable, and
    the Left / Right arrow keys nudge the offset exactly like the
    − / ＋ buttons beside it (keyboard parity for the stepper)."""
    def __init__(self, deck):
        super().__init__('0')
        self._deck = deck
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Left:
            self._deck.window.cycle_bar_offset(self._deck.index, -1)
        elif event.key() == Qt.Key.Key_Right:
            self._deck.window.cycle_bar_offset(self._deck.index, +1)
        else:
            super().keyPressEvent(event)


class Deck(QFrame):
    def __init__(self, window, index):
        super().__init__()
        self.window, self.index = window, index
        self.setAcceptDrops(True)
        self.setObjectName('deck')
        # Pass 15: no per-deck colour any more — the fader fill, the
        # chip bar and the knob indicators all wear the same neutral
        # greys on both decks (see the pass-15 palette note).
        # Channel fader styling (vertical, mixer-strip style): for a
        # vertical slider Qt paints the region above the handle (toward
        # maximum) as the sub-page, so the fill colour goes on the
        # add-page — the fill then rises from the bottom up to the
        # handle, like a level. The handle is a wide bar with a centre
        # grip line. Pass 6 (user direction, 2026-11-26): the fader is
        # about 3x thicker than the pass-3 original — groove 8 -> 24px,
        # handle 30 -> 84px wide — a fat track that reads and grabs
        # easily. The slider also gets an explicit 84px width below:
        # the stylesheet's negative handle margins alone do not widen
        # the widget (verified in the pass-6 render — the handle was
        # clipped to the groove), so the width is reserved in code and
        # the handle paints across it.
        # Pass 9: the fader cap gains machined horizontal ridges —
        # alternating light bands across the cap with the dark
        # centre grip line kept.
        fader_qss = (f'QSlider::groove:vertical {{width:24px;background:#0c0d0e;border:1px solid {COLOR_BORDER};border-radius:3px}}'
                     f'QSlider::sub-page:vertical {{background:#0c0d0e;border-radius:3px}}'
                     f'QSlider::add-page:vertical {{background:{COLOR_FADER_FILL};border-radius:3px}}'
                     f'QSlider::handle:vertical {{height:20px;width:84px;margin:0 -30px;background:qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #b9b9b9, stop:0.16 #e6e6e6, stop:0.32 #c6c6c6, stop:0.46 #ececec, stop:0.5 #141518, stop:0.54 #ececec, stop:0.68 #c6c6c6, stop:0.84 #e6e6e6, stop:1 #a8a8a8);border:2px solid #000000;border-radius:3px}}')
        self.fader_qss = fader_qss
        # Pass 9 — the deck is now a wide STRIP (the user's sketch):
        # transport buttons stacked at the left end, the title /
        # readout block beside them, the big flowing waveform filling
        # the rest of the row, and the pads in a row under the
        # waveform. The channel strip (fader + knobs) no longer lives
        # here — Window gathers both decks' into the mixer block at
        # the bottom left (see the fader_box / knob_box built below).
        grid = QGridLayout(self)
        grid.setSpacing(2)
        grid.setContentsMargins(0, 0, 0, 0)
        # Pass 13: the strip's height goes to the waveform row —
        # height freed elsewhere in the window (notice / summary
        # line removed) lands here, not in the pads row.
        grid.setRowStretch(0, 1)
        # Transport: three buttons stacked at the strip's left end,
        # before the title. Same buttons, same wiring as the old row.
        # Pass 20 (user review fix, 2026-11-27: 「左上の読み込みとか
        # 3つの要素はabともに上から同じ並びに」): Deck B's stack no
        # longer mirrors Deck A's — BOTH decks read top-to-bottom
        # 読込 / 再生・一時停止 / 先頭.
        self.load = QPushButton('Load / 読込')
        self.load.clicked.connect(self.choose)
        self.play = QPushButton('▶ Play / 再生')
        self.play.clicked.connect(lambda: window.engine.command('play', index, not window.engine.decks[index]['playing']))
        self.cue = QPushButton('■ Reset / 先頭')
        self.cue.clicked.connect(self.reset)
        transport_box = QWidget()
        transport_box.setFixedWidth(108)
        transport = QVBoxLayout(transport_box)
        transport.setContentsMargins(0, 0, 0, 0)
        transport.setSpacing(4)
        for button in (self.load, self.play, self.cue):
            # Pass 24: slimmer vertical padding (STYLE's
            # [transport="true"] rule) — the stack's minimum
            # height gives ~15px back to short screens; the
            # buttons read the same at design size.
            button.setProperty('transport', True)
            transport.addWidget(button)
        transport.addStretch()
        grid.addWidget(transport_box, 0, 0)
        # Info block: deck chip + big title, key sub-line, and the
        # BPM / clock readouts in a row under them.
        # Info block: deck chip + big title, key sub-line, and the
        # BPM / clock readouts in a row under them. Pass 14: the
        # block is an old-LCD window set into the deck chassis —
        # its text is ink on the pale glass, the chip an ink-outline
        # box (its left bar keeps the deck colour, a chassis-side
        # accent), and the readouts are LCD windows of their own.
        info_box = LcdWindow()
        self.info_box = info_box
        info_box.setObjectName('lcdinfo')
        info_box.setFixedWidth(292)
        info = QVBoxLayout(info_box)
        info.setContentsMargins(8, 3, 8, 3)
        info.setSpacing(1)
        title_row = QHBoxLayout()
        chip = QLabel('DECK '+('A' if index == 0 else 'B'))
        chip.setStyleSheet(f'color:{COLOR_LCD_INK};background:transparent;font-size:12px;font-weight:800;padding:4px 8px;border:1px solid {COLOR_LCD_INK};border-left:6px solid {COLOR_LCD_INK_SOFT};border-radius:2px')
        title_row.addWidget(chip)
        self.name = QLabel('No track loaded / 未読込')
        self.name.setStyleSheet(f'color:{COLOR_LCD_INK};font-size:17px;font-weight:800;letter-spacing:0.3px')
        self.name.setWordWrap(True)
        title_row.addWidget(self.name, 1)
        info.addLayout(title_row)
        self.sub = QLabel('—')
        self.sub.setStyleSheet(f'color:{COLOR_LCD_INK_SOFT};font-size:11px')
        info.addWidget(self.sub)
        readouts = QHBoxLayout()
        readouts.setSpacing(4)
        # Pass 11: the readout is a dot-matrix console display
        # (pass 12: drawn in the bundled DotGothic16, like everything).
        # Fixed width sized to its longest text at its pitch, so
        # the digits never clip (the old LCD label squeezed the
        # clock's last digit at the box edge).
        # Pass 20 (user review fix, 2026-11-27: 「読み込んだ曲のと
        # ころにはbpmいらなくて 残り時間はほしいね」): the BPM
        # readout that sat beside the clock is GONE (the track BPM
        # still feeds the SET BPM dial's fallback and the set
        # list's BPM column — only this figure leaves), and the
        # clock shows the remaining + elapsed pair again, undoing
        # pass 16 for the DECK clocks only (the MIX TIMER stays
        # elapsed-only).
        # Pass 24: height 28 -> 22 (the 16px glyph stays centred
        # and unclipped; the readout's padding gives the pixels).
        self.clock = DotReadout(pitch=1.55, color=COLOR_LCD_INK_SOFT, height=22)
        self.clock.setFixedWidth(164)
        self.clock.setText('00:00.0')
        readouts.addWidget(self.clock)
        # Pass 28 (user report 2026-11-28: 「小節があってない」):
        # the bar-offset stepper, beside the clock — analysis
        # finds the beat phase but not which beat starts the bar,
        # so the user cycles the downbeat 0..3 beats here. It
        # moves the waveform's bar ticks and feeds the Live
        # export's bar alignment; Window.cycle_bar_offset owns
        # the state change + analysis-cache persistence.
        self.bar_label = QLabel('')
        self.bar_label.setStyleSheet(
            f'color:{COLOR_LCD_INK_SOFT};font-size:11px;font-weight:700')
        self.bar_label.setToolTip(i18n.tr('bar_offset_tip', window.lang))
        readouts.addWidget(self.bar_label)
        self.bar_minus = QPushButton('−')
        self.bar_plus = QPushButton('＋')
        for button, step in ((self.bar_minus, -1), (self.bar_plus, +1)):
            button.setFixedSize(20, 20)
            button.setStyleSheet(
                f'color:{COLOR_LCD_INK};background:transparent;'
                'border:1px solid rgba(34,43,28,0.35);border-radius:2px;'
                'font-size:12px;font-weight:800;padding:0')
            button.setToolTip(i18n.tr('bar_offset_tip', window.lang))
            button.clicked.connect(
                lambda _checked=False, s=step:
                window.cycle_bar_offset(index, s))
            readouts.addWidget(button)
        self.bar_value = BarOffsetLabel(self)
        self.bar_value.setFixedSize(18, 20)
        self.bar_value.setStyleSheet(
            f'color:{COLOR_LCD_INK};font-size:12px;font-weight:800')
        self.bar_value.setToolTip(i18n.tr('bar_offset_tip', window.lang))
        readouts.addWidget(self.bar_value)
        readouts.addStretch()
        info.addLayout(readouts)
        self.refresh_bar_offset()
        # Pass 15 state markers (user direction, 2026-11-27): ON AIR
        # — a FILLED ink chip, lit only while this deck is actually
        # playing — and MONITOR — a HOLLOW ink chip that is also the
        # headphone-monitor toggle for THIS deck (clicking it is the
        # toggle; Window.toggle_monitor owns the state — per-deck
        # since pass 28, both decks may be monitored at once).
        # Filled vs hollow: the two states cannot be confused, even
        # in monochrome. Both chips live on the glass, in ink.
        state_row = QHBoxLayout()
        state_row.setSpacing(4)
        self.onair = QLabel('')
        self.onair.setFixedHeight(19)   # pass 24: 22 -> 19 (11px text + its 2px padding still fits)
        self.onair.setAlignment(Qt.AlignmentFlag.AlignCenter)
        state_row.addWidget(self.onair)
        self.monitor_btn = QPushButton('')
        self.monitor_btn.setFixedHeight(19)   # pass 24: matches the ON AIR chip
        self.monitor_btn.clicked.connect(
            lambda: window.toggle_monitor(index))
        state_row.addWidget(self.monitor_btn)
        # Pass 33 (user request 2026-11-28: 「bpm変えても音変わら
        # ないようにできる？」): the KEY LOCK chip, third in the
        # state row — a deck-state toggle belongs with ON AIR /
        # MONITOR, and the info block's fixed width keeps it off
        # the layout-minimum budget a seventh pad would have blown
        # (pass 24's window-fit floor). Checkable, starts ON (the
        # engine's default); the engine command goes through the
        # same toggled signal as the pads, and Window.tick keeps
        # the chip following the engine state. Screen-only: the
        # DDJ-RB pad tuple in midi_input.py is an explicit
        # six-button list and stays that way.
        self.key_lock = QPushButton('')
        self.key_lock.setCheckable(True)
        self.key_lock.setChecked(True)
        self.key_lock.setFixedHeight(19)   # matches the state chips
        self.key_lock.setToolTip(i18n.tr('key_lock_tip', window.lang))
        self.key_lock.toggled.connect(
            lambda on, i=index: window.engine.command('key_lock', i, on))
        self.key_lock.toggled.connect(self.set_key_lock_chip)
        state_row.addWidget(self.key_lock)
        state_row.addStretch()
        info.addLayout(state_row)
        self.onair_on = None
        self.monitor_on = None
        self.key_lock_on = None
        self.set_onair(False)
        self.set_monitor(False)
        self.set_key_lock_chip(True)
        info.addStretch()
        grid.addWidget(info_box, 0, 1)
        self.wave = Waveform(COLOR_WAVE_BLUE)
        self.wave.setSizePolicy(QSizePolicy.Policy.Expanding,
                                QSizePolicy.Policy.Expanding)
        self.wave.seek.connect(lambda x: window.engine.command('seek', index, int(x*len(self.wave.track.audio))))
        grid.addWidget(self.wave, 0, 2)
        grid.setColumnStretch(2, 1)
        # Channel strip parts — built here, PLACED by Window in the
        # mixer block (pass 9 sketch layout): the fader in its own
        # box, the five knobs (GAIN / HIGH / MID / LOW / FILTER,
        # centre = neutral) in their own column box. Only the
        # widgets' homes changed — the value domain (0-100), the
        # defaults (parameters 50; the channel fader starts at
        # 100 = unity since pass 28, where the old 80 start was
        # one of the stacked attenuations behind 「音が小さい」)
        # and every engine command below are exactly what they
        # sent from inside the deck.
        self.fader_box = QWidget()
        fader_box = QVBoxLayout(self.fader_box)
        fader_box.setContentsMargins(0, 0, 0, 0)
        fader_box.setSpacing(4)
        self.volume_label = QLabel('CHANNEL\nVOLUME')
        self.volume_label.setStyleSheet(SECTION_LABEL_QSS)
        self.volume_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        fader_box.addWidget(self.volume_label)
        self.volume = QSlider(Qt.Orientation.Vertical)
        self.volume.setStyleSheet(fader_qss)
        self.volume.setRange(0, 100)
        self.volume.setValue(100)   # pass 28: unity start (was 80)
        self.volume.setFixedWidth(84)
        self.volume.setFixedHeight(240)
        self.volume.valueChanged.connect(lambda v: window.engine.command('volume', index, v/100))
        fader_box.addWidget(self.volume)
        self.knob_box = QWidget()
        knob_column = QVBoxLayout(self.knob_box)
        knob_column.setContentsMargins(0, 0, 0, 0)
        # Pass 18: spacing 0 — the taller knob cells (64px, ring
        # clearance) carry their own breathing room now, and the
        # window's height budget needs the pixels (see Window).
        knob_column.setSpacing(0)
        self.controls = {}
        # Pass 17 set the captions to GAIN / (none) / (none) / LOW /
        # FILTER (the 'EQ' prefix gone, HIGH / MID uncaptioned);
        # pass 20 (user review fix, 2026-11-27: 「hi mid の文字が
        # ない」) restores the bare band names — the final caption
        # row is GAIN / HI / MID / LOW / FILTER, and the letters
        # 'EQ' still appear nowhere. All five knobs stay, fully
        # wired. Pass 20 also gives GAIN and FILTER the muted-amber
        # accent colour (COLOR_KNOB_ACCENT — indicator + arc only);
        # HI / MID / LOW keep the neutral grey.
        for label, action, default in [('GAIN', 'gain', 50), ('HI', 'eq_high', 50),
                                       ('MID', 'eq_mid', 50), ('LOW', 'eq_low', 50),
                                       ('FILTER', 'filter', 50)]:
            knob = Knob(label,
                        COLOR_KNOB_ACCENT if action in ('gain', 'filter')
                        else COLOR_TEXT_SUB,
                        bipolar=(action != 'gain'))
            knob.setRange(0, 100); knob.setValue(default)
            if action == 'gain':
                knob.valueChanged.connect(lambda v, i=index: window.engine.command('gain', i, v/50))
            else:
                knob.valueChanged.connect(lambda v, a=action, i=index: window.engine.command(a, i, v/100))
            self.controls[action] = knob
            knob_column.addWidget(knob)
        # Performance pads (pass 9): one row of six under the
        # waveform, aligned with the strip grid's wave column. Same
        # six buttons, same wiring as the old 3x2 grid.
        cue_set = QPushButton('Cue Set'); cue_set.clicked.connect(lambda: window.engine.command('cue_set', index))
        cue_jump = QPushButton('Cue'); cue_jump.clicked.connect(lambda: window.engine.command('cue_jump', index))
        hot1 = QPushButton('Hot 1'); hot1.clicked.connect(lambda: window.engine.command('hotcue_jump', index, 1))
        hot1s = QPushButton('Hot1 Set'); hot1s.clicked.connect(lambda: window.engine.command('hotcue_set', index, 1))
        # Kept as attributes so the Window's language registry
        # (pass 10) can re-label the row on a language switch.
        self.pad_cue_set, self.pad_cue = cue_set, cue_jump
        self.pad_hot1, self.pad_hot1s = hot1, hot1s
        quant = QPushButton('Quantize'); quant.setCheckable(True)
        self.quant = quant
        quant.toggled.connect(lambda on, i=index: window.engine.command('quantize', i, on))
        sync = QPushButton('SYNC'); sync.setCheckable(True)
        self.sync = sync
        sync.setToolTip('Tempo + beat-phase sync / テンポ＋拍位相同期（グリッドが確かな時）。音程も変わります。')
        # Pass 35: the pad is modal now — Window.on_sync_pad
        # owns what a press means (AUTO SYNC on: switch the
        # mode off; off: the pass-26 manual engage). The DDJ-RB
        # pad clicks this same button (midi_input), so the
        # hardware follows for free.
        sync.toggled.connect(lambda on, i=index: window.on_sync_pad(i, on))
        pads_row = QHBoxLayout()
        pads_row.setSpacing(5)
        for button in [cue_set, cue_jump, hot1, hot1s, quant, sync]:
            button.setProperty('pad', True)
            # Pass 24: the six pads' text widths set the deck
            # strip's minimum width (~500px of it). A 52px floor
            # lets a narrow window compress the row instead of
            # pushing the whole window's minimum past 1100px;
            # at design width the buttons size to their text
            # exactly as before.
            button.setMinimumWidth(52)
            pads_row.addWidget(button)
        grid.addLayout(pads_row, 1, 2)
    def refresh_bar_offset(self):
        """Pass 28: repaint the bar-offset stepper from the loaded
        track (0 and disabled while the deck is empty — there is
        no grid to shift)."""
        track = self.wave.track if hasattr(self, 'wave') else None
        value = track.bar_offset if track is not None else 0
        if hasattr(self, 'bar_value'):
            self.bar_value.setText(str(value))
            for button in (self.bar_minus, self.bar_plus):
                button.setEnabled(track is not None)

    def set_onair(self, on, force=False):
        """Pass 15: the ON AIR chip + the info window's heavy ink
        frame follow the engine's real playing state for this deck
        (Window.tick drives this). Filled ink when playing; a faint
        hollow outline when not — the marker is only ever ON for
        the deck actually on air."""
        on = bool(on)
        if self.onair_on == on and not force:
            return
        self.onair_on = on
        self.onair.setText(i18n.tr('on_air', self.window.lang))
        if on:
            self.onair.setStyleSheet(
                f'background:{COLOR_LCD_INK};color:{COLOR_LCD_PALE};'
                f'border:1px solid {COLOR_LCD_INK};border-radius:2px;'
                'font-size:11px;font-weight:800;padding:2px 8px')
        else:
            self.onair.setStyleSheet(
                f'background:transparent;color:{COLOR_LCD_INK_DIM};'
                'border:1px solid rgba(34,43,28,0.35);border-radius:2px;'
                'font-size:11px;font-weight:700;padding:2px 8px')
        self.info_box.set_heavy_frame(on)
    def set_key_lock_chip(self, on, force=False):
        """Pass 33: paint the KEY LOCK chip — FILLED ink while ON
        (an active function, like ON AIR), hollow while OFF (like
        MONITOR off). The text itself is the language registry's
        ('key_lock'); this only paints, and Window.tick calls it
        with the engine's state so the chip follows the deck even
        when the checked state was set under blocked signals."""
        on = bool(on)
        if self.key_lock_on == on and not force:
            return
        self.key_lock_on = on
        if on:
            self.key_lock.setStyleSheet(
                f'background:{COLOR_LCD_INK};color:{COLOR_LCD_PALE};'
                f'border:1px solid {COLOR_LCD_INK};border-radius:2px;'
                'font-size:11px;font-weight:800;padding:2px 8px')
        else:
            self.key_lock.setStyleSheet(
                f'background:transparent;color:{COLOR_LCD_INK_DIM};'
                'border:1px solid rgba(34,43,28,0.35);border-radius:2px;'
                'font-size:11px;font-weight:700;padding:2px 8px')

    def set_monitor(self, on, force=False):
        """Pass 15: the MONITOR chip's state — HOLLOW ink (heavy
        outline, no fill) while this deck is monitored, so it can
        never be confused with the filled ON AIR chip. Pass 28:
        the state lives in Window.monitor_decks (per-deck, both
        may be on); this only paints it (and re-texts on a
        language switch, via force)."""
        on = bool(on)
        if self.monitor_on == on and not force:
            return
        self.monitor_on = on
        self.monitor_btn.setText(
            i18n.tr('monitor_on' if on else 'monitor_off',
                    self.window.lang))
        if on:
            self.monitor_btn.setStyleSheet(
                f'background:transparent;color:{COLOR_LCD_INK};'
                f'border:2px solid {COLOR_LCD_INK};border-radius:2px;'
                'font-size:11px;font-weight:800;padding:1px 8px')
        else:
            self.monitor_btn.setStyleSheet(
                f'background:transparent;color:{COLOR_LCD_INK_DIM};'
                'border:1px solid rgba(34,43,28,0.35);border-radius:2px;'
                'font-size:11px;font-weight:700;padding:2px 8px')
    def paintEvent(self, event):
        # Pass 11: corner screws over the deck's styled surface,
        # same as the PanelFrame panels.
        super().paintEvent(event)
        _paint_screws(QPainter(self), self.width(), self.height())
    def reset(self):
        self.window.engine.command('play', self.index, False)
        self.window.engine.command('seek', self.index, 0)
    def choose(self):
        path,_ = QFileDialog.getOpenFileName(self,'Audio','','Audio (*.wav *.mp3 *.flac *.aiff *.aif)')
        if path: self.window.load(self.index,path)
    def dragEnterEvent(self,event):
        if event.mimeData().hasUrls(): event.acceptProposedAction()
    def dropEvent(self,event):
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        for n,path in enumerate(paths[:2]): self.window.load((self.index+n)%2,path)

class Window(QMainWindow):
    def __init__(self, audio=True):
        super().__init__()
        # Pass 12: register the bundled dot font before any widget
        # is built, whatever entry point created the window.
        load_dot_font()
        self.setWindowTitle('MixAble DJ Studio • Mw-fActory • Phase 2 Trial')
        self._fit_done = False
        self.resize(1280,900)
        # Pass 23 (window fit, user report 2026-11-27: the window
        # opened larger than his screen — at >100% Windows display
        # scaling 1280x900 logical overflows a 1080p panel): clamp
        # the opening size to the primary screen's available
        # geometry and centre there. The root lives in a QScrollArea
        # (below), so a clamped window stays fully usable — the
        # scroll area absorbs what does not fit. The window itself
        # is freely resizable/maximizable (resize() only; no fixed
        # size is set on the window anywhere).
        # Pass 24: that clamp compared the CLIENT size against the
        # available geometry, but the available geometry is in
        # FRAME terms — on Windows the opened window overflowed
        # the work area by the title-bar/border size (~30px), so
        # the bottom slid under the taskbar even after the clamp
        # (「最大化すると下がツールバーより下にいってきれちゃう」 /
        # 「縦横比がおかしい」). showEvent now re-fits the FRAME
        # into the available geometry once the frame size is real.
        # A sane minimum size keeps drag-resizing from crushing
        # the window into a postage stamp — sized so the minimum
        # plus a window frame still fits an 800px-wide screen.
        self.setMinimumSize(760, 500)
        screen = QApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            fit_w = min(1280, avail.width())
            fit_h = min(900, avail.height())
            self.resize(fit_w, fit_h)
            self.move(avail.left() + (avail.width() - fit_w) // 2,
                      avail.top() + (avail.height() - fit_h) // 2)
        self.engine = Engine()
        # Pass 26: SET BPM follow state — the base the set runs at
        # (set list's SET BPM, else Deck A's track BPM) and the
        # current value, which a TEMPO fader / dial move may have
        # shifted within ±6 BPM of the base. The dial displays
        # set_bpm_now; refresh_set_bpm_dial owns the base.
        self._set_bpm_base = None
        self.set_bpm_now = None
        # Pass 35: the watcher tracks (sync_mode, sync_bar)
        # pairs — the bar outcome of an AUTO SYNC engage is a
        # status transition of its own.
        self._sync_mode_seen = [(None, None), (None, None)]
        self.stream = None
        # Pass 37: the cue side of the audio state — a second
        # stream (paired endpoints) or the second channel pair
        # of the master stream (single 4ch) carries the
        # headphone cue bus when the hardware offers one.
        self.cue_stream = None
        self.audio_mode = None      # plan kind that opened
        self._cue_live = False
        self._cue_blocks = deque(maxlen=8)
        self._cue_lock = threading.Lock()
        self.audio_status_count = 0
        self.callback_count = 0
        self.callback_seconds = 0.0
        self.callback_max_seconds = 0.0
        self.hostapi_name = ''
        self.audio_device_name = ''
        # Pass 25: audio ladder diagnostics + retry state (see
        # start_audio / _maybe_retry_audio). _audio_attempted
        # gates the retry: a window built with audio=False and
        # never started stays inert under tick().
        self.audio_diag = None
        self._audio_attempted = False
        self._audio_on_rb = False
        self._audio_on_fallback = False
        self._audio_signature = ()
        self._audio_retry_due = 0.0
        self._audio_last_attempt = 0.0
        self._midi_was_connected = False
        self.pool = ThreadPoolExecutor(max_workers=2)
        self.result = Result()
        self.result.ready.connect(self.loaded)
        self.loading = set()
        self.setlist_data = None   # loaded set list (normalized dict)
        self._setlist_cursor = None   # pass 32: browse cursor identity
        self.clip_tracks = []      # setlist_to_clip_tracks() result, for the Live hand-off
        self.deck_a_bpm = None     # Deck A track BPM — fallback for the centre SET BPM dial
        self.mix_timer = new_mix_timer()   # pass 8: performance elapsed timer
        # Pass 15: the headphone-monitor select. The engine has no
        # separate cue bus, so this state is the surface's monitor
        # select, and its real job in the engine is the transition
        # audition's TARGET: preview() runs the transition INTO
        # the monitored deck.
        # Pass 28 (user direction, 2026-11-28: 「モニターはどちら
        # か一方だけでなく両方できるようにしてほしい」): the
        # select is PER-DECK now — monitor_decks is the set of
        # decks being monitored (both may be in it), and
        # _monitor_order remembers enable order so the
        # monitor_deck property can still name the most recently
        # enabled deck (the audition's target rule).
        self.monitor_decks = set()
        self._monitor_order = []
        # Pass 27 (user report, 2026-11-28: 「ミックスリストと演奏
        # 中の曲の相互連携が取れてなくて矛盾してる」): the set
        # list's played / now-playing / next markers are derived
        # LIVE from the engine's decks (see _update_setlist_claims)
        # — never from the loaded file's started / current_index
        # snapshot, which froze the rows wherever the phone page
        # had left them while the decks played something else.
        # _setlist_played is the ledger of rows that have sounded;
        # _deck_claims[i] is the row deck i currently speaks for.
        self._setlist_played = set()
        self._deck_claims = [None, None]
        # Pass 29 (user report, 2026-11-28: 「トラックリストと再生
        # 中のものの矛盾も解決してない」): pass 27 kept off-list
        # plays from touching the planned rows, but a deck playing
        # a file the list never heard of then left the panel
        # claiming a plan reality had left behind (his list said
        # one track waiting while the decks played two others).
        # Now an off-list load earns an ACTUAL row — in load
        # order, with its real deck chip and a live status from
        # the same claims machinery — rendered ABOVE the planned
        # rows, which keep their pass-27 behaviour untouched.
        # _actual_rows[i] is a normalized entry plus 'deck';
        # _actual_played is its sounded ledger; _actual_claims[i]
        # is the actual row deck i currently has loaded.
        self._actual_rows = []
        self._actual_played = set()
        self._actual_claims = [None, None]
        self._setlist_sig = None   # last rendered (statuses, monitor path)
        # Pass 10 (user direction, 2026-11-27): ONE language at a
        # time — the bilingual labels looked cluttered (「文字ガチャ
        # ガチャして」). self.lang is the window's language; every
        # static label registers a setter in self._labels and
        # set_language() reapplies the whole registry; dynamic texts
        # (play/pause, timer captions, set-list rows, the status bar)
        # re-render from i18n at their usual refresh points. The
        # status bar goes through self._status_fn so a language
        # switch re-renders the CURRENT message, not a stale one.
        self.lang = i18n.DEFAULT_LANG
        i18n.set_current(self.lang)
        self._labels = []   # [(apply_fn)] — apply_fn(lang) re-labels one widget
        self._status_fn = lambda lang: i18n.tr('ready', lang)
        data = Path(os.getenv('LOCALAPPDATA', str(Path.home()/'.local/share'))) / 'MixCraftDJStudio'
        self.data_dir = data
        self.settings = load_app_settings(data)
        # Pass 28: the analysis cache is wired in at last —
        # load_track was built for it, but the app never passed
        # one, so every load re-analysed from scratch and a
        # per-track correction (the bar offset) had nowhere to
        # persist. A cache failure must never break loading:
        # cache=None is load_track's long-standing fallback.
        try:
            data.mkdir(parents=True, exist_ok=True)
            self.cache = AnalysisCache(data / 'analysis.db')
        except Exception:
            self.cache = None
        self.journal = Journal(data/'sessions'/f'{time.time_ns()}.jsonl')
        root = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(root)
        self.setCentralWidget(scroll)
        layout = QVBoxLayout(root)
        # Pass 9 structure (the user's hand-drawn sketch, 2026-11-27;
        # proportions explicitly provisional — he will revisit them):
        # header / TOP BAND (deck strips stacked + SET BPM cluster) /
        # utility bar / BOTTOM ZONE (mixer block left, big set list
        # right) / status. Everything fits 1280x900 without scrolling.
        # Pass 12 retune: DotGothic16's line heights run ~11px
        # taller over the whole window than the old stacks, so the
        # root margins/spacing (and the status size below) give
        # those pixels back — 1280x900 still fits uncut.
        # Pass 18: the root margins/spacing give 14px back to the
        # height budget — the knob cells grew by 12px each (ring
        # clearance) and the window still fits 1280x900 unscrolled.
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(1)
        # Slim dark header bar (rekordbox toolbar style) — product name
        # and version stay, the big light title block is gone.
        header_bar = PanelFrame()
        header_bar.setObjectName('headerbar')
        header = QHBoxLayout(header_bar)
        header.setContentsMargins(10, 2, 10, 2)
        title = QLabel(f'MixAble  <span style="color:{COLOR_TEXT_SUB}">DJ STUDIO</span>')
        title.setStyleSheet('font-size:16px;font-weight:800;letter-spacing:0.5px')
        header.addWidget(title)
        # Pass 13 (user direction 2026-11-27: 「情報量もへらしてい
        # こう」): the header's subtitle and the right-side meta line
        # (MW-FACTORY / OFFLINE / version) are gone — the brand title
        # and the language toggle are all the header carries now.
        header.addStretch()
        # Language toggle (pass 10): a small segmented pair at the
        # header's right end — EN | 日本語, each labelled in its own
        # language (the standard convention). The checked button
        # wears the global QPushButton:checked style.
        self.lang_en = QPushButton('EN')
        self.lang_en.setObjectName('langbtn')
        self.lang_en.setCheckable(True)
        self.lang_en.clicked.connect(lambda: self.set_language('en'))
        self.lang_jp = QPushButton('日本語')
        self.lang_jp.setObjectName('langbtn')
        self.lang_jp.setCheckable(True)
        self.lang_jp.clicked.connect(lambda: self.set_language('jp'))
        lang_box = QWidget()
        lang_row = QHBoxLayout(lang_box)
        lang_row.setContentsMargins(6, 0, 0, 0)
        lang_row.setSpacing(2)
        lang_row.addWidget(self.lang_en)
        lang_row.addWidget(self.lang_jp)
        header.addWidget(lang_box)
        layout.addWidget(header_bar)
        # TOP BAND (pass 9 — the user's hand-drawn sketch, 2026-11-27):
        # the two deck strips STACKED wide, Deck A over Deck B, and at
        # the right end the big SET BPM dial with the two level lamps
        # beside it. The mixer block and the set list form the bottom
        # zone further down (after the utility bar).
        self.decks = [Deck(self,i) for i in range(2)]
        top_band = QHBoxLayout()
        strips = QVBoxLayout()
        strips.setSpacing(2)
        strips.addWidget(self.decks[0], 1)
        strips.addWidget(self.decks[1], 1)
        top_band.addLayout(strips, 1)
        bpm_frame = PanelFrame()
        bpm_frame.setObjectName('bpmframe')
        bpm_frame.setMinimumWidth(200)
        bpm_lay = QVBoxLayout(bpm_frame)
        # Pass 35: tight spacing — the AUTO SYNC chip joins this
        # column, and the window's layout minimum (the pass-24
        # any-size criterion, pinned by test_window_fit) must
        # not grow for it. The stretch below absorbs the design
        # height, so the cluster simply sits a little denser.
        bpm_lay.setSpacing(2)
        # One SET BPM dial for the whole set (pass 5, user direction
        # 2026-11-26): both decks run at the fixed set BPM. Display
        # only — refresh_set_bpm_dial() feeds it from the loaded set
        # list, falling back to Deck A's track BPM.
        self.set_bpm_label = QLabel('SET BPM / セットBPM')
        self.set_bpm_label.setStyleSheet(SECTION_LABEL_QSS)
        self.set_bpm_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bpm_lay.addWidget(self.set_bpm_label)
        self.set_dial = JogDial(caption='SET BPM', size=148)
        # Pass 26: the dial is a control now — dragging/scrolling
        # it moves the SET BPM through the same set_set_bpm door
        # the DDJ-RB TEMPO faders use.
        self.set_dial.on_change = self.set_set_bpm
        bpm_lay.addWidget(self.set_dial, 0, Qt.AlignmentFlag.AlignCenter)
        # The level meter (MeterLamp keeps the QProgressBar contract
        # tick() already used — setRange / setValue / setToolTip).
        # Pass 9 drew it as the sketch's two small lamps; pass 14
        # makes it an LCD segment bar (see the class).
        self.level_label = QLabel('LEVEL')
        self.level_label.setStyleSheet(SECTION_LABEL_QSS)
        self.level_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bpm_lay.addWidget(self.level_label)
        self.meter = MeterLamp()
        self.meter.setRange(0,100)
        bpm_lay.addWidget(self.meter, 0, Qt.AlignmentFlag.AlignCenter)
        # Pass 35 (AUTO SYNC): the set-level mode chip under the
        # meter — filled ink while ON (the default), like the
        # decks' KEY LOCK chips. A deck starting against a
        # playing deck then syncs itself (tempo + beat phase +
        # bar line). The SYNC pads switch the mode off; this
        # chip is the way back on (and the manual pads return
        # while it is off — see on_sync_pad).
        self.auto_sync_chip = QPushButton('')
        self.auto_sync_chip.setCheckable(True)
        self.auto_sync_chip.setChecked(True)
        self.auto_sync_chip.setFixedHeight(19)
        self.auto_sync_chip.setToolTip(i18n.tr('auto_sync_tip', self.lang))
        self.auto_sync_chip.toggled.connect(self._on_auto_sync_chip)
        self.auto_sync_on = None
        bpm_lay.addWidget(self.auto_sync_chip, 0,
                          Qt.AlignmentFlag.AlignCenter)
        self.set_auto_sync_chip(True, force=True)
        bpm_lay.addStretch()
        top_band.addWidget(bpm_frame)
        # Pass 13: the top band shares the window's flexible height
        # with the bottom zone (was: bottom zone took it all), so
        # the pixels freed by the declutter grow the waveforms as
        # well as the set list.
        layout.addLayout(top_band, 1)
        # Set list panel: independent of the decks. Loads the JSON exported
        # by the phone prep page (setlist.load_setlist) and shows the
        # track order with the phone design's status colours.
        setlist_panel = SetlistDropPanel()
        setlist_panel.setObjectName('setlist')
        # Pass 24: files dropped on the panel join the set list.
        setlist_panel.drop_handler = self.add_dropped_to_setlist
        self.setlist_panel = setlist_panel
        setlist_layout = QVBoxLayout(setlist_panel)
        # Pass 9: the browser header and the MIX TIMER share one
        # bar — the performance clock belongs with the set it times,
        # and merging the two rows hands their height to the track
        # rows below. Same widgets, same wiring as pass 8.
        setlist_head = QHBoxLayout()
        setlist_head.setSpacing(8)
        self.setlist_label = QLabel('BROWSER / SET LIST')
        self.setlist_label.setStyleSheet(SECTION_LABEL_QSS)
        setlist_head.addWidget(self.setlist_label)
        # Mix timer (pass 8, user direction 2026-11-27: 「今何分ミック
        # スしたとかもわかるといいね」): how long the performance has
        # been running, at a glance — MIX mm:ss elapsed, and small
        # START/STOP + RESET controls. The timer auto-starts on the
        # first deck play; the math lives in engine.py (pure,
        # unit-tested) and tick() drives it with time.monotonic().
        # Pass 16 (user direction 2026-11-27: 「残りの時間もいらね
        # な」): the remaining-to-target readout that pass 8 added
        # beside it is gone — elapsed only, as originally asked.
        # (Pass 9: the MIX TIMER caption label left the head row —
        # the readout's own 'MIX' prefix says it, and the row shares
        # the bottom zone's width with the mixer block now.)
        # Pass 11: the MIX TIMER readouts are dot-matrix too
        # (pass 12: kanji caption and digits alike in DotGothic16 —
        # see DotReadout).
        self.mix_elapsed = DotReadout(pitch=2.3, align='center', height=30)
        self.mix_elapsed.setFixedWidth(134)
        self.mix_elapsed.setText('MIX 00:00')
        setlist_head.addWidget(self.mix_elapsed)
        setlist_head.addStretch()
        self.mix_toggle = QPushButton('START / 開始')
        self.mix_toggle.clicked.connect(self.toggle_mix_timer)
        setlist_head.addWidget(self.mix_toggle)
        self.mix_reset = QPushButton('RESET / リセット')
        self.mix_reset.clicked.connect(self.reset_mix_timer)
        setlist_head.addWidget(self.mix_reset)
        self.setlist_open = QPushButton('開く / Open setlist JSON')
        self.setlist_open.setToolTip('セットリストを開く / Open setlist JSON')
        self.setlist_open.clicked.connect(self.open_setlist)
        setlist_head.addWidget(self.setlist_open)
        setlist_layout.addLayout(setlist_head)
        setlist_layout.setSpacing(5)
        # Pass 13: the summary line under the head row ('セットBPM
        # 122 • 5 曲 • 合計 24:19 • 再生中 2 曲目') is removed — the
        # SET BPM duplicates the dial, the now-playing state
        # duplicates the green row, and the total duplicates the mix
        # timer's remaining math. The rows speak for themselves.
        # Pass 14: the column headers and the rows live inside an
        # old-LCD window set into the panel — the head row (timer
        # readouts, hardware buttons) stays on the chassis above it,
        # like the display surround on real gear.
        lcd_list = LcdWindow()
        lcd_list.setObjectName('lcdlist')
        lcd_layout = QVBoxLayout(lcd_list)
        lcd_layout.setContentsMargins(4, 4, 4, 4)
        lcd_layout.setSpacing(3)
        # Browser-style column header for the track rows below.
        columns = QHBoxLayout()
        columns.setContentsMargins(6, 2, 6, 2)
        # Offset matching the row layout: deck bar 5 + chip 22 +
        # status bar 6 + number 34 + the four 6px gaps between them.
        columns.addSpacing(5 + 6 + 22 + 6 + 6 + 6 + 34 + 6)
        self.col_title = QLabel('TITLE / ARTIST')
        self.col_title.setStyleSheet(LCD_LABEL_QSS)
        columns.addWidget(self.col_title, 1)
        self.col_heads = {}
        for key, text, width, align in [('bpm', 'BPM', 64, Qt.AlignmentFlag.AlignRight),
                                   ('key', 'KEY', 56, Qt.AlignmentFlag.AlignCenter),
                                   ('time', 'TIME', 52, Qt.AlignmentFlag.AlignRight),
                                   ('status', 'STATUS', 84, Qt.AlignmentFlag.AlignCenter)]:
            if key == 'status':
                # Pass 15: keep the STATUS head over the rows' badge
                # column, past the rows' monitor-marker slot.
                columns.addSpacing(58)
            head = QLabel(text)
            head.setStyleSheet(LCD_LABEL_QSS)
            head.setFixedWidth(width)
            head.setAlignment(align)
            columns.addWidget(head)
            self.col_heads[key] = head
        # Pass 13: no trailing spacing — the rows' old rightmost
        # slot (an empty dark box on every row without a link) is
        # gone, so the header ends with the STATUS column too.
        lcd_layout.addLayout(columns)
        self.setlist_rows = QVBoxLayout()
        self.setlist_rows.setSpacing(2)
        lcd_layout.addLayout(self.setlist_rows)
        # Pass 13: pin the contents to the top — the panel's surplus
        # height would otherwise spread between the head row, the
        # column headers and the rows, leaving a hole above the
        # first row. The stretch takes it all, below the last row,
        # browser-style (inside the LCD window now: empty glass).
        lcd_layout.addStretch(1)
        setlist_layout.addWidget(lcd_list, 1)
        # Added to the window layout further down: the set list is the
        # browser panel, so it sits at the bottom (rekordbox layout).
        # Pass 9: transition audition and audio output share ONE
        # slim utility bar between the player and the browser (they
        # are both set-up tools, not performance controls) — one row
        # instead of a caption line plus two rows. Same widgets,
        # same wiring; the audition's fine print rides as a tooltip.
        self.utility_bar = PanelFrame()
        utility_bar = self.utility_bar
        utility_bar.setObjectName('utility')
        utility_bar.setToolTip('Aの末尾 → Bの先頭（テンポ同期なし）')
        utility = QHBoxLayout(utility_bar)
        utility.setContentsMargins(8, 2, 8, 2)
        utility.setSpacing(6)
        # Pass 13: the bar's small section captions ('試聴' / '出力')
        # are gone — the controls (duration combo, preview buttons,
        # device combo, apply) stay exactly as wired and read fine
        # without them.
        # Pass 20 (user review fix, 2026-11-27: 「まんなかひだりの
        # 8秒って意味がわからない」): ONE caption comes back — the
        # seconds combo was the one control whose meaning did not
        # read without it, so it gets its name beside it: 試聴の
        # 長さ / AUDITION LENGTH (the audition's length in seconds).
        self.audition_label = QLabel('AUDITION LENGTH')
        self.audition_label.setStyleSheet(SECTION_LABEL_QSS)
        utility.addWidget(self.audition_label)
        self.duration = QComboBox()
        # Pass 10: the items are re-texted on a language switch, so
        # the combo must keep fitting its contents (the default
        # adjust-on-first-show would freeze it at the first
        # language's width and clip '8 seconds' after a switch).
        self.duration.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        for seconds in [8,16,32,64]: self.duration.addItem(f'{seconds} seconds / 秒',seconds)
        utility.addWidget(self.duration)
        self.preview_button = QPushButton('▶ Preview transition / ミックス試聴')
        self.preview_button.clicked.connect(self.preview)
        utility.addWidget(self.preview_button)
        self.preview_stop = QPushButton('Stop preview / 試聴終了')
        self.preview_stop.clicked.connect(lambda:self.engine.command('stop_preview'))
        utility.addWidget(self.preview_stop)
        self.export_button = QPushButton('Save events JSON / 操作保存')
        self.export_button.clicked.connect(self.export)
        utility.addWidget(self.export_button)
        # Pass 21: the performance → Live export sits beside the
        # events export — same recorded performance, two products:
        # the raw log (操作保存) and a Live set to open and fix
        # (Live用に書き出し).
        self.live_button = QPushButton('EXPORT FOR LIVE / Live用に書き出し')
        self.live_button.clicked.connect(self.export_live)
        utility.addWidget(self.live_button)
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.VLine)
        divider.setStyleSheet(f'color:{COLOR_BORDER_HI}')
        utility.addWidget(divider)
        self.devices = QComboBox()
        # The device list can hold very long names; the combo is the
        # bar's one flexible element, so let it shrink to fit.
        self.devices.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.devices.setMinimumContentsLength(12)
        self.devices.addItem('Automatic / WASAPI優先', 'auto')
        self.devices.addItem('System default / システム既定', None)
        try:
            hosts = sd.query_hostapis()
            for i,d in enumerate(sd.query_devices()):
                if d['max_output_channels'] >= 2: self.devices.addItem(d['name']+' / '+hosts[d['hostapi']]['name'],i)
        except Exception: pass
        utility.addWidget(self.devices,1)
        self.apply_button = QPushButton('Apply / 適用')
        self.apply_button.clicked.connect(self.start_audio)
        utility.addWidget(self.apply_button)
        # Pass 36: the diagnostics export sits with the audio
        # controls it describes — one click refreshes the ladder
        # record and carries it to the Desktop (see
        # export_diagnostics). Never disabled: it matters most
        # exactly when the audio state is failed or fallback.
        self.diag_button = QPushButton('EXPORT DIAGNOSTICS / 診断を書き出す')
        self.diag_button.clicked.connect(self.export_diagnostics)
        utility.addWidget(self.diag_button)
        # Pass 24: the utility bar's controls wear the slimmer
        # padding (STYLE's [util="true"] rules) — the bar's height
        # is part of the window's floor on short screens.
        for util_widget in (self.duration, self.preview_button,
                            self.preview_stop, self.export_button,
                            self.live_button, self.devices,
                            self.apply_button, self.diag_button):
            util_widget.setProperty('util', True)
        layout.addWidget(utility_bar)
        # BOTTOM ZONE (pass 9 sketch): left, one compact MIXER BLOCK
        # gathering both decks' channel strips — from outside in:
        # Deck A fader | Deck A knobs | Deck B knobs | Deck B fader
        # (faders on the OUTER sides, knob columns in the middle, per
        # the user's standing rule), the MASTER knob at the block's
        # top centre, and the CROSSFADER along its bottom. Right, the
        # SET LIST gets the large remaining area.
        mixer = PanelFrame()
        mixer.setObjectName('mixer')
        mix = QVBoxLayout(mixer)
        mix.setSpacing(2)
        mix_top = QHBoxLayout()
        self.mixer_label = QLabel('MIXER')
        self.mixer_label.setStyleSheet(SECTION_LABEL_QSS)
        mix_top.addWidget(self.mixer_label)
        mix_top.addStretch()
        # MASTER volume as a knob (pass 6): same Knob widget and the
        # same wiring the horizontal slider had — range 0-100,
        # 'master' command with v/100. Pass 28: the default is 100
        # (unity) — the old 80 start stacked a second -1.9 dB on
        # the channel fader's 80 (「音が小さい」, measured in
        # tests/test_pass28_gain_chain.py). Pass 20: the master wears
        # the muted-amber accent (like GAIN / FILTER) — it belongs
        # to neither deck, the accent marks it as a master control.
        self.master = Knob('MASTER', COLOR_KNOB_ACCENT, bipolar=False)
        self.master.setRange(0, 100)
        self.master.setValue(100)
        self.master.valueChanged.connect(lambda v: self.engine.command('master', value=v/100))
        mix_top.addWidget(self.master)
        mix_top.addStretch()
        mix_top.addSpacing(54)   # balance the MIXER caption's width
        mix.addLayout(mix_top)
        channels = QHBoxLayout()
        channels.setSpacing(8)
        channels.addWidget(self.decks[0].fader_box)
        channels.addWidget(self.decks[0].knob_box)
        channel_divider = QFrame()
        channel_divider.setFrameShape(QFrame.Shape.VLine)
        channel_divider.setStyleSheet(f'color:{COLOR_BORDER_HI}')
        channels.addWidget(channel_divider)
        channels.addWidget(self.decks[1].knob_box)
        channels.addWidget(self.decks[1].fader_box)
        mix.addLayout(channels)
        mix.addStretch()
        cross_head = QHBoxLayout()
        deck_a_label = QLabel('A')
        deck_a_label.setStyleSheet(f'color:{COLOR_TEXT_SUB};font-size:15px;font-weight:800')
        cross_head.addWidget(deck_a_label)
        cross_head.addStretch()
        self.cross_label = QLabel('CROSSFADER')
        self.cross_label.setStyleSheet(SECTION_LABEL_QSS)
        cross_head.addWidget(self.cross_label)
        cross_head.addStretch()
        deck_b_label = QLabel('B')
        deck_b_label.setStyleSheet(f'color:{COLOR_TEXT_SUB};font-size:15px;font-weight:800')
        cross_head.addWidget(deck_b_label)
        mix.addLayout(cross_head)
        self.cross = QSlider(Qt.Orientation.Horizontal)
        self.cross.setRange(0,1000)
        self.cross.setValue(500)
        # The crossfader's machined treatment: a neutral dark track
        # (pass 15: the old blue/red end tints were deck colour
        # coding), and the cap carries the same ridged gradient as
        # the channel fader caps.
        self.cross.setStyleSheet(
            f'QSlider::groove:horizontal {{height:10px;background:qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #26282e, stop:0.5 #060607, stop:1 #26282e);border:1px solid {COLOR_BORDER};border-radius:3px}}'
            f'QSlider::sub-page:horizontal {{background:transparent}}'
            f'QSlider::add-page:horizontal {{background:transparent}}'
            'QSlider::handle:horizontal {width:16px;height:28px;margin:-10px 0;'
            'background:qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #b9b9b9, stop:0.16 #e6e6e6, stop:0.32 #c6c6c6, stop:0.46 #ececec, stop:0.5 #141518, stop:0.54 #ececec, stop:0.68 #c6c6c6, stop:0.84 #e6e6e6, stop:1 #a8a8a8);'
            'border:2px solid #000000;border-radius:3px}')
        self.cross.valueChanged.connect(lambda v:self.engine.command('cross',value=v/1000))
        mix.addWidget(self.cross)
        bottom = QHBoxLayout()
        bottom.addWidget(mixer)
        bottom.addWidget(setlist_panel, 1)
        layout.addLayout(bottom, 1)
        self.status = QLabel('Ready / 準備完了')
        self.status.setObjectName('status')
        layout.addWidget(self.status)
        # Pass 13: the bottom notice paragraph (the 'BPM・キーは推定
        # 値。SYNCは…' fine print) is removed entirely — the SYNC
        # caveat still rides on the SYNC pad's tooltip, and the freed
        # height goes to the deck strips / set list below.
        self._register_labels()
        self._apply_status()
        self.render_setlist()
        self.refresh_mix_timer()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(40)
        if audio: self.start_audio()
        # Pass 23: DDJ-RB MIDI input — auto-connects in the
        # background (poll() runs off this same tick); every
        # hardware gesture is performed on the on-screen widgets,
        # so it takes the identical command path into the engine
        # and the performance journal. Absent hardware or library
        # changes nothing for mouse use.
        self.midi = MidiController(self)
    # ------------------------------------------------------------------
    # Language registry (pass 10): every static label registers an
    # apply_fn(lang) here once; set_language() replays the registry
    # and then re-renders the dynamic texts. Strings live in i18n.py.
    # ------------------------------------------------------------------
    def _reg(self, setter, key):
        self._labels.append(lambda lang, s=setter, k=key: s(i18n.tr(k, lang)))
    def _register_labels(self):
        for deck in self.decks:
            self._reg(deck.load.setText, 'load')
            self._reg(deck.cue.setText, 'reset')
            self._reg(deck.volume_label.setText, 'channel_volume')
            self._reg(deck.pad_cue_set.setText, 'cue_set')
            self._reg(deck.pad_cue.setText, 'cue')
            self._reg(deck.pad_hot1.setText, 'hot1')
            self._reg(deck.pad_hot1s.setText, 'hot1_set')
            self._reg(deck.quant.setText, 'quantize')
            self._reg(deck.sync.setText, 'sync')
            self._reg(deck.sync.setToolTip, 'sync_tip')
            self._reg(deck.key_lock.setText, 'key_lock')
            self._reg(deck.key_lock.setToolTip, 'key_lock_tip')
            # Pass 28: the bar-offset stepper's caption + tips.
            self._reg(deck.bar_label.setText, 'bar_offset')
            self._reg(deck.bar_label.setToolTip, 'bar_offset_tip')
            self._reg(deck.bar_minus.setToolTip, 'bar_offset_tip')
            self._reg(deck.bar_plus.setToolTip, 'bar_offset_tip')
            self._reg(deck.bar_value.setToolTip, 'bar_offset_tip')
        self._reg(self.set_bpm_label.setText, 'set_bpm')
        self._reg(self.set_dial.set_caption, 'set_bpm')
        self._reg(self.set_dial.setToolTip, 'set_bpm_tip')
        # Pass 35: the AUTO SYNC chip's label + tip.
        self._reg(self.auto_sync_chip.setText, 'auto_sync')
        self._reg(self.auto_sync_chip.setToolTip, 'auto_sync_tip')
        self._reg(self.level_label.setText, 'level')
        self._reg(self.utility_bar.setToolTip, 'audition_tip')
        self._reg(self.audition_label.setText, 'audition_length')
        self._reg(self.preview_button.setText, 'preview')
        self._reg(self.preview_stop.setText, 'stop_preview')
        self._reg(self.export_button.setText, 'save_events')
        self._reg(self.live_button.setText, 'export_live')
        self._reg(self.apply_button.setText, 'apply')
        self._reg(self.diag_button.setText, 'diag_export')
        self._reg(self.setlist_label.setText, 'setlist_caption')
        self._reg(self.mix_reset.setText, 'timer_reset')
        self._reg(self.setlist_open.setText, 'open_setlist')
        self._reg(self.setlist_open.setToolTip, 'open_setlist')
        self._reg(self.col_title.setText, 'col_title')
        self._reg(self.col_heads['time'].setText, 'col_time')
        self._reg(self.col_heads['status'].setText, 'col_status')
        self._reg(self.mixer_label.setText, 'mixer_caption')
        self._reg(self.master.set_label, 'master')
        self._reg(self.cross_label.setText, 'crossfader')
        # Pass 15: the state chips' texts are language-dependent
        # too — re-apply both decks' current states on a switch.
        def state_texts(lang):
            for deck in self.decks:
                deck.set_onair(deck.onair_on, force=True)
                deck.set_monitor(deck.monitor_on, force=True)
        self._labels.append(state_texts)
        # The two combos rebuild / re-text their static items,
        # keeping the current selection (by item data).
        def duration_items(lang):
            current = self.duration.currentData()
            self.duration.blockSignals(True)
            self.duration.clear()
            for seconds in (8, 16, 32, 64):
                self.duration.addItem(
                    i18n.tr('seconds_item', lang).format(n=seconds), seconds)
            index = self.duration.findData(current)
            if index >= 0: self.duration.setCurrentIndex(index)
            self.duration.blockSignals(False)
        self._labels.append(duration_items)
        def device_items(lang):
            if self.devices.count() >= 2:
                self.devices.setItemText(0, i18n.tr('device_auto', lang))
                self.devices.setItemText(1, i18n.tr('device_default', lang))
        self._labels.append(device_items)
        for apply_fn in self._labels:
            apply_fn(self.lang)
        self.lang_en.setChecked(self.lang == 'en')
        self.lang_jp.setChecked(self.lang == 'jp')
        self._refresh_deck_placeholders()
    def set_language(self, lang):
        """Header toggle handler: switch the whole window to one
        language, immediately — registry first, then the dynamic
        texts (status bar, mix timer, set-list rows, deck play
        buttons via the next tick) re-render in the new language."""
        if lang not in i18n.LANGS: return
        self.lang = lang
        i18n.set_current(lang)
        for apply_fn in self._labels:
            apply_fn(lang)
        self.lang_en.setChecked(lang == 'en')
        self.lang_jp.setChecked(lang == 'jp')
        self._apply_status()
        self.refresh_mix_timer()
        self.render_setlist()
        self._refresh_deck_placeholders()
        for deck in self.decks:
            deck.wave.update()
    def _apply_status(self):
        self.status.setText(self._status_fn(self.lang))
    def _render_deck_key(self, deck, track):
        """Deck info sub-line (pass 13): ONE key notation — the
        Camelot code, the same notation as the set list's KEY
        column. The key name ('A minor') is not deleted, it rides
        as the sub-line's tooltip instead of a second notation in
        parentheses."""
        shown = track.camelot or track.key
        deck.sub.setText(i18n.tr('key_line', self.lang).format(camelot=shown)
                         if shown else '—')
        deck.sub.setToolTip(track.key if track.camelot and track.key else '')
    def _refresh_deck_placeholders(self):
        """Per-deck info text on a language switch (and after a set
        list opens). The widget-side wave.track is the UI's truth
        for 'loaded' (the engine applies its command queue lazily,
        at render time). Empty decks show the localized placeholder;
        loaded decks re-derive their header title from the known
        metadata (pass 13: set list / file tags, file name last) and
        get their Key sub-line re-rendered from the track the
        waveform already holds."""
        for index, deck in enumerate(self.decks):
            if index in self.loading:
                continue
            track = deck.wave.track
            if track is None:
                deck.name.setText(i18n.tr('no_track', self.lang))
            else:
                deck.name.setText(deck_title_text(track.path,
                                                  self.setlist_data))
                self._render_deck_key(deck, track)
    def _open_stream(self, device, extra, channels=2, callback=None):
        # Pass 23: the block size stays 512 frames — the top of
        # the stable 256-512 range for the DDJ-RB's ASIO driver at
        # 44.1/48 kHz — and latency='low' asks PortAudio for the
        # device's own suggested low latency rather than a fixed
        # guess. (Underruns are what the user hears as ぶつぶつ.)
        # Pass 37: channels/callback are parameters now — a 4ch
        # stream carries master + cue in one callback, and the
        # paired topology runs a second stream for the cue pair.
        kwargs = dict(samplerate=RATE, channels=channels, blocksize=512,
            latency='low', dtype='float32', device=device,
            callback=callback or self.callback)
        if extra is not None: kwargs['extra_settings'] = extra
        stream = sd.OutputStream(**kwargs)
        stream.start()
        return stream

    def _close_streams(self):
        """Stop and close every open stream (master + cue)."""
        for attr in ('cue_stream', 'stream'):
            stream = getattr(self, attr, None)
            if stream is not None:
                try:
                    stream.stop(); stream.close()
                except Exception:
                    pass
                setattr(self, attr, None)
        self._cue_live = False
        with self._cue_lock:
            self._cue_blocks.clear()

    def _audio_plans(self, selection):
        """Pass 37: the ordered output topologies start_audio
        tries. Each plan is a dict(kind, jobs, modes, hostapi,
        device): jobs are (role, device, channels, callback)
        tuples that must ALL open for the plan to live; modes
        are (extra_settings, exclusive) pairs tried in order.
        'auto' climbs audio_config.plan_auto() — 4ch single
        stream, paired WASAPI endpoints, RB master-only, then
        the defaults. An explicit pick keeps the pre-pass-37
        behaviour exactly: one 2ch plan on that device (WASAPI
        exclusive, then shared), then the system default."""
        def wasapi_modes():
            return [(sd.WasapiSettings(exclusive=True), True),
                    (sd.WasapiSettings(exclusive=False), False)]
        if selection == 'auto':
            plans = []
            for plan in plan_auto(sd):
                callback = self.callback
                if plan['kind'] == 'single4':
                    callback = self.callback_cue4
                elif plan['kind'] == 'paired':
                    callback = self.callback_paired_master
                jobs = [('master', plan['device'], plan['channels'],
                         callback)]
                if plan['kind'] == 'paired':
                    jobs.append(('cue', plan['cue_device'], 2,
                                 self.callback_cue_paired))
                modes = wasapi_modes() \
                    if 'WASAPI' in plan['hostapi'] else [(None, False)]
                plans.append(dict(kind=plan['kind'], jobs=jobs,
                                  modes=modes,
                                  hostapi=plan['hostapi'],
                                  device=plan['device']))
            # Pass 25's honesty contract: when RB topologies
            # were tried and failed, the landing is the SYSTEM
            # default and the record says 'fallback' — the
            # WASAPI-default-device plan is the primary choice
            # only when no RB plan exists at all (the old rung
            # 3), never a quiet landing pad after an RB failure.
            if any(p['kind'] in ('single4', 'paired', 'master_only')
                   for p in plans):
                plans = [p for p in plans
                         if p['kind'] != 'default'
                         or p['device'] is None]
            return plans
        device, hostapi_name = resolve_output(selection, sd)
        if device is None:
            extra = sd.WasapiSettings(exclusive=False) \
                if 'WASAPI' in hostapi_name else None
            modes = [(extra, False)]
        elif 'WASAPI' in hostapi_name:
            modes = wasapi_modes()
        else:
            modes = [(None, False)]
        return [dict(kind='explicit',
                     jobs=[('master', device, 2, self.callback)],
                     modes=modes, hostapi=hostapi_name,
                     device=device),
                dict(kind='default',
                     jobs=[('master', None, 2, self.callback)],
                     modes=[(None, False)], hostapi='', device=None)]

    def start_audio(self):
        self._audio_attempted = True
        self._audio_last_attempt = time.monotonic()
        self._close_streams()
        selection = self.devices.currentData()
        # Pass 25: the ladder is diagnosable. The device landscape
        # is captured BEFORE opening; every open attempt below
        # records its outcome; the record lands in
        # audio-diagnostics.json beside the settings and on the
        # device picker's tooltip. His 2026-11-28 fall to the PC's
        # own speakers must never be a silent mystery again.
        try:
            landscape = diagnose_ladder(sd)
        except Exception:
            landscape = None
        rb_indices = set()
        device_names = {}
        if landscape:
            rb_indices = {d['index'] for d in landscape['rb_devices']}
            device_names = {d['index']: d['name']
                            for d in landscape['devices']}
            self._audio_signature = tuple(sorted(
                (d['index'], d['name'], d['hostapi'])
                for d in landscape['rb_devices']))
        # Pass 23/24: for a WASAPI device the low-latency path is
        # EXCLUSIVE mode first, then shared; an ASIO device is
        # tried directly. Pass 37: 'auto' no longer resolves to
        # ONE device — it walks plan_auto()'s topologies (4ch
        # single stream, paired endpoints, RB master-only) ahead
        # of the defaults, so the DDJ-RB makes the sound whenever
        # it offers any path, and the headphone cue bus rides the
        # 4-channel paths. A device that will not open must never
        # leave the app silent — the last plan is always the
        # system default, and the status bar says when we landed
        # there.
        try:
            plans = self._audio_plans(selection)
        except Exception:
            plans = [dict(kind='default',
                          jobs=[('master', None, 2, self.callback)],
                          modes=[(None, False)], hostapi='',
                          device=None)]
        first_error = None
        opened = None
        attempt_records = []
        targeted_name = None
        for plan in plans:
            for extra, exclusive in plan['modes']:
                streams = {}
                plan_ok = True
                for role, device, channels, callback in plan['jobs']:
                    if targeted_name is None and device is not None:
                        targeted_name = device_names.get(device) \
                            or plan['hostapi'] or str(device)
                    record = dict(
                        device=device,
                        name=device_names.get(device)
                             if device is not None else None,
                        hostapi=plan['hostapi']
                                if device is not None else '',
                        exclusive=bool(exclusive), ok=False,
                        error=None, channels=channels, role=role,
                        kind=plan['kind'])
                    try:
                        streams[role] = self._open_stream(
                            device, extra, channels=channels,
                            callback=callback)
                    except Exception as error:
                        record['error'] = str(error)
                        attempt_records.append(record)
                        if first_error is None:
                            first_error = error
                        plan_ok = False
                        break
                    record['ok'] = True
                    attempt_records.append(record)
                if plan_ok:
                    opened = (plan, streams, exclusive)
                    break
                for stream in streams.values():
                    try:
                        stream.stop(); stream.close()
                    except Exception:
                        pass
            if opened is not None:
                break
        diag = dict(at=time.strftime('%Y-%m-%d %H:%M:%S'),
                    selection=str(selection),
                    hosts=landscape['hosts'] if landscape else [],
                    rb_devices=landscape['rb_devices'] if landscape
                               else [],
                    rungs=landscape['rungs'] if landscape else [],
                    attempts=attempt_records, outcome='',
                    mode=None, cue=False)
        rb_open_error = None
        for record in attempt_records:
            if record['device'] in rb_indices and record['error']:
                rb_open_error = record['error']
                break
        if opened is None:
            diag['outcome'] = 'silent'
            self._finish_audio_diag(diag, on_rb=False, on_fallback=False)
            self._status_fn = lambda lang, e=first_error: \
                i18n.tr('audio_error', lang).format(error=e)
            self._apply_status()
            return
        plan, streams, opened_exclusive = opened
        opened_device = plan['device']
        hostapi_name = plan['hostapi']
        device_name = device_names.get(opened_device) \
            if opened_device is not None else None
        self.stream = streams.get('master')
        self.cue_stream = streams.get('cue')
        self.audio_mode = plan['kind']
        self._cue_live = plan['kind'] in ('single4', 'paired')
        diag['mode'] = plan['kind']
        diag['cue'] = self._cue_live
        if opened_device is None and targeted_name is not None:
            diag['outcome'] = 'system_default_fallback'
            self._finish_audio_diag(
                diag, on_rb=False, on_fallback=True)
            self.hostapi_name = ''
            self.audio_device_name = ''
            failed = targeted_name
            if rb_open_error:
                # Pass 25: an RB device was THERE but would not
                # open — name the likeliest cause as a possibility.
                self._status_fn = lambda lang, d=failed: \
                    i18n.tr('audio_fallback', lang).format(device=d) \
                    + ' • ' + i18n.tr('audio_rb_busy_hint', lang)
            else:
                self._status_fn = lambda lang, d=failed: \
                    i18n.tr('audio_fallback', lang).format(device=d)
            self._apply_status()
            return
        diag['outcome'] = 'opened'
        self._finish_audio_diag(
            diag, on_rb=opened_device in rb_indices, on_fallback=False)
        self.hostapi_name = hostapi_name
        self.audio_device_name = device_name or ''
        host_display = hostapi_name or 'default host'
        if opened_exclusive:
            host_display += ' EXCLUSIVE'
        latency_ms = (self.stream.latency or 0) * 1000
        # Pass 37: the status line is honest about the cue bus —
        # live on the 4-channel paths, explicitly unavailable on
        # a master-only RB (the controller makes the sound, but
        # there is no second pair for the headphones).
        note = ''
        if self._cue_live:
            note = 'cue'
        elif plan['kind'] == 'master_only':
            note = 'no_cue'
        self._status_fn = lambda lang, h=host_display, \
                d=device_name or 'system default', \
                ms=f'{latency_ms:.0f}', n=note: \
            i18n.tr('audio_active', lang).format(host=h, device=d,
                                                 ms=ms) \
            + (' • ' + i18n.tr('audio_cue_on', lang) if n == 'cue'
               else ' • ' + i18n.tr('audio_no_cue', lang)
               if n == 'no_cue' else '')
        self._apply_status()
    def _finish_audio_diag(self, diag, on_rb, on_fallback):
        """Pass 25: store the ladder record, write it beside the
        settings, refresh the retry state and the picker surfaces."""
        self.audio_diag = diag
        self._audio_on_rb = bool(on_rb)
        self._audio_on_fallback = bool(on_fallback)
        save_audio_diagnostics(self.data_dir, diag)
        self._refresh_audio_surfaces(diag)
    def _refresh_audio_surfaces(self, diag):
        """Pass 25: the ladder outcome, where he can find it —
        the device picker's tooltip carries every RB device's
        state (in use / could not open + the error / not tried /
        not found), and each RB entry in the picker itself wears
        the same state as its own tooltip."""
        by_device = {}
        for record in diag.get('attempts', []):
            if record.get('device') is not None:
                by_device[record['device']] = record
        rb_by_index = {d['index']: d
                       for d in diag.get('rb_devices', [])}
        def state_of(index):
            record = by_device.get(index)
            if record is not None and record.get('ok'):
                return i18n.tr('audio_state_opened', self.lang)
            if record is not None and record.get('error'):
                return i18n.tr('audio_state_failed', self.lang) \
                    .format(error=record['error'])
            return i18n.tr('audio_state_untried', self.lang)
        lines = []
        for index in sorted(rb_by_index):
            d = rb_by_index[index]
            lines.append(f"{d['name']} / {d['hostapi']}: "
                         f"{state_of(index)}")
        if not lines:
            lines.append('DDJ-RB: '
                         + i18n.tr('audio_state_absent', self.lang))
        self.devices.setToolTip('\n'.join(lines))
        for row in range(self.devices.count()):
            data = self.devices.itemData(row)
            if isinstance(data, int) and data in rb_by_index:
                d = rb_by_index[data]
                self.devices.setItemData(
                    row, f"{d['name']}: {state_of(data)}",
                    Qt.ItemDataRole.ToolTipRole)
    def export_diagnostics(self):
        """Pass 36 (promised 2026-11-28 after the ASIO diagnosis
        stalled for hours on fetching a file out of AppData by
        hand): one click refreshes the ladder record — a fresh
        open attempt at the current selection is what writes
        audio-diagnostics.json (start_audio, the Apply path) —
        then copies the file to the Desktop under a findable
        name and says, in plain words, where it went and what
        the record says is happening. No gating on a healthy
        stream: the failed and fallback states are exactly when
        this is needed."""
        try:
            self.start_audio()
        except Exception:
            pass
        dest = copy_audio_diagnostics(self.data_dir, desktop_dir())
        if dest is None:
            QMessageBox.information(
                self, i18n.tr('diag_title', self.lang),
                i18n.tr('diag_missing', self.lang))
            return
        summary = audio_diag_summary(
            read_audio_diagnostics(self.data_dir), self.lang)
        QMessageBox.information(
            self, i18n.tr('diag_title', self.lang),
            i18n.tr('diag_saved', self.lang).format(
                name=dest.name, summary=summary))
    def _audio_rb_signature(self):
        """The visible DDJ-RB outputs as a comparable tuple —
        the audio retry's change detector (pass 25)."""
        try:
            return tuple(sorted(
                (d['index'], d['name'], d['hostapi'])
                for d in diagnose_ladder(sd)['rb_devices']))
        except Exception:
            return ()
    def _maybe_retry_audio(self, now):
        """Pass 25: audio re-selection with MIDI's retry
        discipline. Only in 'auto' mode (an explicit pick is
        always respected), only after audio was started once,
        and only while the sound is NOT on the DDJ-RB: a dead
        stream retries every few seconds; a stream parked on a
        fallback retries when the RB picture changes (a device
        appeared, a busy driver freed — retried at least once a
        minute while an RB is visible) or the moment the
        DDJ-RB's MIDI connects, since MIDI presence proves the
        hardware is attached and awake."""
        midi_now = bool(getattr(self.midi, 'connected', False)) \
            if getattr(self, 'midi', None) is not None else False
        midi_just_connected = midi_now and not self._midi_was_connected
        self._midi_was_connected = midi_now
        if not getattr(self, '_audio_attempted', False):
            return
        if self.devices.currentData() != 'auto':
            return
        if self._audio_on_rb:
            return
        if self.stream is None:
            if now >= self._audio_retry_due:
                self._audio_retry_due = now + 6.0
                self.start_audio()
            return
        if midi_just_connected:
            self.start_audio()
            return
        if now >= self._audio_retry_due:
            self._audio_retry_due = now + 6.0
            signature = self._audio_rb_signature()
            stale = now - self._audio_last_attempt >= 60.0
            if signature != self._audio_signature \
                    or (signature and stale):
                self.start_audio()
    def callback_cue4(self, outdata, frames, time_info, status):
        """Pass 37: one 4-channel stream — the master mix on
        channels 1/2, the headphone cue bus on 3/4."""
        if status: self.audio_status_count += 1
        self.engine.cue_decks = self.monitor_decks
        cue = np.zeros((frames, 2), dtype='float32')
        outdata[:, 0:2] = self.engine.render(frames, cue)
        outdata[:, 2:4] = cue

    def callback_paired_master(self, outdata, frames, time_info,
                               status):
        """Pass 37: the master endpoint of a WASAPI pair renders
        both mixes; the cue block is handed to the cue
        endpoint's callback through a small queue. The two
        endpoints share the controller's clock; a missed block
        costs a few ms of silence in the headphones, never a
        glitch in the master."""
        if status: self.audio_status_count += 1
        self.engine.cue_decks = self.monitor_decks
        cue = np.zeros((frames, 2), dtype='float32')
        outdata[:] = self.engine.render(frames, cue)
        with self._cue_lock:
            self._cue_blocks.append(cue)

    def callback_cue_paired(self, outdata, frames, time_info,
                            status):
        """Pass 37: the cue endpoint of a WASAPI pair plays the
        blocks the master callback rendered; an empty queue is
        silence, not an error."""
        if status: self.audio_status_count += 1
        block = None
        with self._cue_lock:
            if self._cue_blocks:
                block = self._cue_blocks.popleft()
        if block is None:
            outdata[:] = 0
            return
        n = min(frames, len(block))
        outdata[:n] = block[:n]
        if n < frames:
            outdata[n:] = 0

    def callback(self,outdata,frames,time_info,status):
        started = time.perf_counter()
        if status: self.audio_status_count += 1
        outdata[:] = self.engine.render(frames)
        elapsed = time.perf_counter()-started
        self.callback_count += 1
        self.callback_seconds += elapsed
        self.callback_max_seconds = max(self.callback_max_seconds, elapsed)
    def load(self,index,path):
        if index in self.loading: return
        self.loading.add(index)
        self.decks[index].name.setText(i18n.tr('loading', self.lang)+Path(path).name)
        self.decks[index].load.setEnabled(False)
        def work():
            try: self.result.ready.emit(index,load_track(path, self.cache),'')
            except Exception as e: self.result.ready.emit(index,None,str(e))
        self.pool.submit(work)
    def loaded(self,index,track,error):
        self.loading.discard(index)
        deck = self.decks[index]
        deck.load.setEnabled(True)
        if error:
            deck.name.setText(i18n.tr('load_failed', self.lang))
            QMessageBox.warning(self,'Audio load',error)
            return
        # Pass 26 (rekordbox anchor): if the current set list
        # carries rekordbox cues for this file, the first hot cue
        # anchors the track's beat grid (the user's cue outranks
        # the analysed phase — see engine.apply_cue_anchor).
        for entry in self.clip_tracks:
            cues = entry.get('cues')
            path = entry.get('path')
            if cues and path and os.path.normcase(
                    str(Path(path).resolve())) == os.path.normcase(
                        track.path):
                apply_cue_anchor(track, cues)
                break
        self.engine.command('stop_preview')
        self.engine.command('load',index,track)
        deck.wave.track = track
        # Pass 32, Rule B: a song loaded onto a deck goes to
        # the TOP of the set list. This is the one moment
        # every load route shares (deck drop, 読込 dialog,
        # double-click, DDJ-RB LOAD), so an off-list song's
        # actual row is created / moved to the top here —
        # including a re-drop of the file the deck already
        # holds, which the claims update alone cannot see
        # (its claim never clears). A listed song gets no
        # actual row (pass 29): its claim is the whole story.
        if self.setlist_data is not None:
            planned = (self.setlist_data or {}).get('tracks') or []
            if not any(_path_matches(e.get('path'), track.path)
                       for e in planned):
                idx = self._actual_row_for(index, track.path)
                if idx is not None:
                    self._actual_to_top(idx)
        deck.refresh_bar_offset()   # pass 28: the new track's offset
        deck.name.setText(deck_title_text(track.path, self.setlist_data))
        self._render_deck_key(deck, track)
        # Pass 20: the deck's BPM readout is gone (user review fix)
        # — the analysed BPM still lands in deck_a_bpm below, where
        # it feeds the SET BPM dial's fallback.
        if index == 0:
            self.deck_a_bpm = track.bpm
            self.refresh_set_bpm_dial()
        # Pass 15: a fresh load can move the set list's monitor-row
        # marker (it follows the monitored deck's loaded track).
        self.render_setlist()
    def preview(self):
        if not all(d['track'] for d in self.engine.decks):
            self._status_fn = lambda lang: i18n.tr('load_both_first', lang)
            self._apply_status()
            return
        # Pass 15: the audition runs INTO the monitored deck (the
        # deck being cued up is the one a DJ monitors); with no
        # monitor selection it keeps the original A -> B direction.
        # Pass 28: with both decks monitored, the target is the
        # most recently monitor-ENABLED deck (monitor_deck).
        target = self.monitor_deck if self.monitor_deck is not None else 1
        self.engine.command('preview', deck=target,
                            value=self.duration.currentData())
    @property
    def monitor_deck(self):
        """The most recently monitor-enabled deck that is still
        on, or None — pass 15's single value kept as a read view
        of the pass-28 per-deck state (the audition's target).
        Assigning replaces the whole monitor state (older
        callers and test doubles do this)."""
        return self._monitor_order[-1] if self._monitor_order else None

    @monitor_deck.setter
    def monitor_deck(self, value):
        if value is None:
            self.monitor_decks = set()
            self._monitor_order = []
        else:
            self.monitor_decks = {value}
            self._monitor_order = [value]
        if hasattr(self, 'decks'):
            self._refresh_monitor_buttons()

    def toggle_monitor(self, index):
        """Pass 28 (user direction, 2026-11-28): the MONITOR
        toggle is per-deck and independent — toggling one deck
        never touches the other, and both may be monitored at
        once. The deck chips and the DDJ-RB headphone CUE buttons
        (note 84) each call this for their own deck only.
        Repaints both decks' monitor chips and the set-list row
        markers."""
        if index in self.monitor_decks:
            self.monitor_decks.discard(index)
            self._monitor_order.remove(index)
        else:
            self.monitor_decks.add(index)
            self._monitor_order.append(index)
        self._refresh_monitor_buttons()
        self.render_setlist()
    def _refresh_monitor_buttons(self):
        for i, deck in enumerate(self.decks):
            deck.set_monitor(i in self.monitor_decks, force=True)
    def cycle_bar_offset(self, index, step):
        """Pass 28: cycle a deck track's bar offset (0..3) — the
        stepper buttons and the readout's arrow keys land here.
        The new value rides the Track, persists to the analysis
        cache (so the next load of the same file starts there),
        and repaints the stepper + waveform bar ticks."""
        deck = self.decks[index]
        track = deck.wave.track
        if track is None:
            return
        track.bar_offset = (track.bar_offset + step) % 4
        # Pass 35: a stepped offset is bar INFORMATION — AUTO
        # SYNC may now align this track's bars (the cache write
        # below marks the row known for the next load too).
        track.bar_offset_known = True
        if self.cache is not None:
            try:
                self.cache.set_bar_offset(track.path,
                                          track.bar_offset)
            except Exception:
                pass    # persistence is a courtesy, never a blocker
        deck.refresh_bar_offset()
        deck.wave.update()
    def export(self):
        path,_ = QFileDialog.getSaveFileName(self,'Save events','mixcraft-events.json','JSON (*.json)')
        if path:
            self.journal.drain(self.engine)
            try: self.journal.export(path)
            except OSError as e: QMessageBox.warning(self,'Save failed',str(e))
    def export_live(self):
        """Pass 21 (user direction, 2026-11-27: 「アプリで完結
        Liveで修整が理想」): turn the recorded performance — the
        journal's event log, autosaved all along — into a Live
        set: the user picks their template .als (the one saved
        from Live for the setlist flow) and a destination, and
        perf_als rebuilds the performance into it (Arrangement
        clips at the performed positions + automation envelopes).
        The SET BPM comes from the loaded set list, else Deck A's
        track — without one there is no honest beat grid.

        Pass 24 (user report 2026-11-27: 「ファイルを選ぶになっ
        た」): the base picker opened unexplained, in the app's
        own download folder, and he stalled there. Now the base
        is remembered after the first pick, the picker explains
        itself in plain words and opens at Live's Templates
        folder, an unusable file is refused on the spot with its
        reason, and every outcome — needs, failure, success —
        is a message box, not just a status line."""
        self.journal.drain(self.engine)
        history = list(self.journal.history)
        if not history:
            self._status_fn = lambda lang: \
                i18n.tr('live_need_performance', lang)
            self._apply_status()
            QMessageBox.information(
                self, i18n.tr('live_dialog_title', self.lang),
                i18n.tr('live_need_performance', self.lang))
            return
        set_bpm = None
        if self.setlist_data is not None:
            set_bpm = display_summary(self.setlist_data)['set_bpm']
        if set_bpm is None:
            set_bpm = self.deck_a_bpm
        if not set_bpm:
            self._status_fn = lambda lang: \
                i18n.tr('live_need_tempo', lang)
            self._apply_status()
            QMessageBox.information(
                self, i18n.tr('live_dialog_title', self.lang),
                i18n.tr('live_need_tempo', self.lang))
            return
        base = self._pick_live_base()
        if not base:
            return
        out_dir = self.settings.get('live_out_dir')
        if not out_dir or not Path(out_dir).is_dir():
            out_dir = str(Path(base).parent)
        out, _ = QFileDialog.getSaveFileName(
            self, i18n.tr('live_out_dialog', self.lang),
            str(Path(out_dir) / 'mixcraft-performance.als'),
            'Ableton Live set (*.als)')
        if not out:
            return
        self.settings['live_out_dir'] = str(Path(out).parent)
        save_app_settings(self.data_dir, self.settings)
        # BPM sources for the reconstruction's SYNC math: the
        # tracks currently on the decks (perf_als adds the set
        # list's entries itself).
        bpms = {}
        for deck in self.decks:
            track = deck.wave.track
            if track is not None and track.bpm:
                bpms[track.path] = track.bpm
        # Pass 28: grid sources for the export's bar alignment —
        # the deck tracks' live grids win; any other performed
        # file falls back to the analysis cache's stored grid.
        grids = {}
        for deck in self.decks:
            track = deck.wave.track
            if track is not None:
                grids[track.path] = {
                    'beat_offset': track.beat_offset,
                    'bar_offset': track.bar_offset}
        if self.cache is not None:
            for event in history:
                if event.get('action') != 'load' \
                        or not event.get('value'):
                    continue
                path = event['value']
                if path in grids:
                    continue
                try:
                    row = self.cache.get(path)
                except Exception:
                    row = None
                if row:
                    grids[path] = {
                        'beat_offset': row.get('beat_offset') or 0,
                        'bar_offset': row.get('bar_offset') or 0}
        try:
            report = perf_als.generate(base, history, out,
                                       set_bpm=set_bpm,
                                       setlist_data=self.setlist_data,
                                       bpms=bpms, grids=grids)
        except Exception as error:
            self._status_fn = lambda lang, e=error: \
                i18n.tr('live_export_error', lang).format(error=e)
            self._apply_status()
            QMessageBox.warning(
                self, i18n.tr('live_dialog_title', self.lang),
                i18n.tr('live_export_error_dialog', self.lang)
                .format(error=error))
            return
        clips = report['clips_written']
        envelopes = len(report['envelopes_written'])
        warning = report['warnings'][0] if report['warnings'] else ''

        def status_fn(lang, p=out, c=clips, n=envelopes, w=warning):
            text = i18n.tr('live_export_done', lang).format(
                path=p, clips=c, envelopes=n)
            return text + (f' • {w}' if w else '')
        self._status_fn = status_fn
        self._apply_status()
        done_text = i18n.tr('live_export_done_dialog', self.lang).format(
            path=out, clips=clips, envelopes=envelopes, base=base)
        if report.get('events_copy'):
            done_text += '\n\n' + i18n.tr(
                'live_export_events_copy', self.lang).format(
                path=report['events_copy'])
        if warning:
            done_text += f'\n\n{warning}'
        QMessageBox.information(
            self, i18n.tr('live_dialog_title', self.lang), done_text)
    def _pick_live_base(self):
        """The template .als for the export, or None on cancel.

        Pass 25 priority, the user's own proposal (「もう最初の
        ファイルの中に組み込んでおいて」): a template.als placed
        beside the app wins outright — validated like any base,
        used silently; an invalid one is reported in plain words
        and the flow falls through. Below that, pass 24's order:
        a remembered base is validated and used silently (the
        pick is a once-ever question); a remembered base that
        went bad is reported in plain words and re-picked; a
        fresh pick is validated on the spot: an unusable file
        is refused with its reason and the picker reopens."""
        bundled = bundled_template_path()
        if bundled is not None:
            reason, _donor = perf_als.validate_base(bundled)
            if reason is None:
                return str(bundled)
            QMessageBox.warning(
                self, i18n.tr('live_dialog_title', self.lang),
                i18n.tr('live_base_bundled_bad', self.lang).format(
                    reason=i18n.tr(f'live_base_reason_{reason}',
                                   self.lang)))
        remembered = self.settings.get('live_base_als')
        if remembered:
            reason, _donor = perf_als.validate_base(remembered)
            if reason is None:
                return remembered
            QMessageBox.warning(
                self, i18n.tr('live_dialog_title', self.lang),
                i18n.tr('live_base_remembered_bad', self.lang).format(
                    path=remembered,
                    reason=i18n.tr(f'live_base_reason_{reason}',
                                   self.lang)))
            self.settings.pop('live_base_als', None)
            save_app_settings(self.data_dir, self.settings)
        while True:
            picked = self._ask_live_base()
            if not picked:
                return None
            reason, _donor = perf_als.validate_base(picked)
            if reason is None:
                self.settings['live_base_als'] = picked
                save_app_settings(self.data_dir, self.settings)
                return picked
            QMessageBox.warning(
                self, i18n.tr('live_dialog_title', self.lang),
                i18n.tr('live_base_unusable', self.lang).format(
                    reason=i18n.tr(f'live_base_reason_{reason}',
                                   self.lang)))
    def _ask_live_base(self):
        """The base-.als picker, with the plain-words explanation
        inside it (pass 24). The non-native dialog is what lets
        the explanation label live in the dialog itself; it
        opens at Live's Templates folder when that exists."""
        dialog = QFileDialog(
            self, i18n.tr('live_base_dialog', self.lang),
            live_base_start_dir(self.settings),
            'Ableton Live set (*.als)')
        dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        label = QLabel(i18n.tr('live_base_explain', self.lang))
        label.setWordWrap(True)
        try:
            layout = dialog.layout()
            layout.addWidget(label, layout.rowCount(), 0, 1, -1)
        except Exception:
            pass  # the caption still names the file; never fatal
        if dialog.exec():
            files = dialog.selectedFiles()
            if files:
                self.settings['live_base_dir'] = str(Path(files[0]).parent)
                save_app_settings(self.data_dir, self.settings)
                return files[0]
        return None
    def open_setlist(self):
        path,_ = QFileDialog.getOpenFileName(self,i18n.tr('setlist_dialog', self.lang),'','Setlist (*.json *.als)')
        if not path: return
        # Pass 31: a Live set (.als) comes back as a set list —
        # the return half of the MixAble ⇄ Live exchange. The
        # extracted entries install through the very same path
        # as a phone JSON below (paths preserved, so row
        # double-click deck loads work); the only difference is
        # the status line, which says plainly how many songs
        # were read and how many carry no file.
        no_file = None
        try:
            if path.lower().endswith('.als'):
                imported = als_import.read_als_set(path)
                data = imported['setlist']
                no_file = imported['no_file']
            else:
                data = load_setlist(path)
        except (SetlistFormatError, als_import.AlsError) as error:
            self._status_fn = lambda lang, e=error: \
                i18n.tr('setlist_error', lang).format(error=e)
            self._apply_status()
            return
        self.setlist_data = data
        self.clip_tracks = setlist_to_clip_tracks(data)
        # Pass 27: a newly opened list starts with a clean live
        # state — the file's own played/playing snapshot is not
        # adopted (the decks are the truth, from here on).
        self._reset_setlist_live()
        # Pass 32: the browse cursor starts on the first row
        # of a freshly opened list (Rule C).
        self._cursor_to_first()
        self.render_setlist()
        # Pass 13: a set list opened after the decks were loaded
        # brings their 'Title — Artist' metadata with it — refresh
        # the deck headers from it.
        self._refresh_deck_placeholders()
        self.refresh_set_bpm_dial()
        summary = display_summary(data)
        name, count = Path(path).name, summary['track_count']
        total, clips = summary['total_text'], len(self.clip_tracks)
        if no_file is None:
            self._status_fn = lambda lang: \
                i18n.tr('setlist_loaded', lang).format(
                    name=name, count=count, total=total, clips=clips)
        else:
            self._status_fn = lambda lang, n=no_file: \
                i18n.tr('setlist_loaded_als', lang).format(
                    name=name, count=count, total=total, nofile=n)
        self._apply_status()
    def toggle_mix_timer(self):
        now = time.monotonic()
        if self.mix_timer['running']:
            mix_timer_stop(self.mix_timer, now)
        else:
            mix_timer_start(self.mix_timer, now)
        self.refresh_mix_timer()
    def reset_mix_timer(self):
        mix_timer_reset(self.mix_timer)
        self.refresh_mix_timer()
    def refresh_mix_timer(self):
        # Pass 16: elapsed only — no remaining / over-target readout.
        elapsed = self.mix_timer['elapsed']
        self.mix_elapsed.setText(f'MIX {format_mix_clock(elapsed)}')
        self.mix_toggle.setText(
            i18n.tr('stop' if self.mix_timer['running'] else 'start', self.lang))
    def refresh_set_bpm_dial(self):
        """Feed the centre SET BPM dial: the loaded set list's set
        BPM wins (the set runs at that fixed tempo); without a set
        list, fall back to Deck A's track BPM; with neither, the
        dial shows its neutral placeholder.

        Pass 26: that value is the set's BASE tempo. When the
        base changes (a new set list, a new Deck A), any tempo
        move is reset, the engine is re-anchored, and the dial
        shows the base; while the base stands, the dial shows
        set_bpm_now — the base as moved by the TEMPO faders /
        dial drag (see set_set_bpm)."""
        bpm = None
        if self.setlist_data is not None:
            bpm = display_summary(self.setlist_data)['set_bpm']
        if bpm is None:
            bpm = self.deck_a_bpm
        if bpm != self._set_bpm_base:
            self._set_bpm_base = bpm
            self.set_bpm_now = bpm
            if bpm:
                self.engine.command('set_bpm_anchor', None, bpm)
        self.set_dial.set_bpm(self.set_bpm_now)

    def set_set_bpm(self, bpm):
        """A master tempo move — the one door used by the SET
        BPM dial's drag/scroll and by the DDJ-RB TEMPO faders
        (via the MIDI controller), so both journal identically.
        Quantized to 0.1 BPM and clamped to the base ±6 BPM
        (the Live Remote Script v1.7 rule); the engine scales
        every deck rate to follow. With no base tempo there is
        nothing to move."""
        if bpm is None or self._set_bpm_base is None:
            return
        bpm = round(float(bpm) * 10) / 10
        bpm = min(max(bpm, self._set_bpm_base - 6.0),
                  self._set_bpm_base + 6.0)
        if self.set_bpm_now is not None \
                and abs(bpm - self.set_bpm_now) < 0.051:
            return
        self.set_bpm_now = bpm
        self.engine.command('set_bpm', None, bpm)
        self.set_dial.set_bpm(bpm)
    def _on_auto_sync_chip(self, on):
        """Pass 35: the AUTO SYNC chip by the SET BPM dial —
        the mode's own switch (the SYNC pads only ever switch
        it off; see on_sync_pad)."""
        self.engine.command('auto_sync', None, bool(on))
        self.set_auto_sync_chip(bool(on))
    def set_auto_sync_chip(self, on, force=False):
        """Pass 35: paint the AUTO SYNC chip — filled ink while
        ON, hollow while OFF, the KEY LOCK chips' language.
        Window.tick calls this with the engine's state so the
        chip follows the mode even when a SYNC pad (not the
        chip) switched it off."""
        on = bool(on)
        if self.auto_sync_on == on and not force:
            return
        self.auto_sync_on = on
        if on:
            self.auto_sync_chip.setStyleSheet(
                f'background:{COLOR_LCD_INK};color:{COLOR_LCD_PALE};'
                f'border:1px solid {COLOR_LCD_INK};border-radius:2px;'
                'font-size:11px;font-weight:800;padding:2px 8px')
        else:
            self.auto_sync_chip.setStyleSheet(
                f'background:transparent;color:{COLOR_LCD_INK_DIM};'
                'border:1px solid rgba(34,43,28,0.35);border-radius:2px;'
                'font-size:11px;font-weight:700;padding:2px 8px')
    def on_sync_pad(self, index, on):
        """Pass 35: the SYNC pad is modal. While AUTO SYNC is
        on, a pad press switches AUTO SYNC off (the chip by the
        SET BPM dial is the way back on) — the pad is the
        mode's off-switch the user's hand is already on. With
        AUTO SYNC off, the pad is exactly the pass-26 manual
        per-deck engage it always was. The pad's lit state is
        not decided here: tick() paints it from the engine's
        per-deck sync flag, as before."""
        if self.engine.auto_sync:
            self.engine.command('auto_sync', None, False)
        else:
            self.engine.command('sync', index, bool(on))
    def standby_deck(self):
        """The deck a set-list double-click loads onto (pass 24):
        the one not playing; when both are stopped (or both are
        somehow playing), the empty one, else Deck B — the
        next-up deck in the app's A/B alternation."""
        decks = self.engine.decks
        if decks[0]['playing'] != decks[1]['playing']:
            return 1 if decks[0]['playing'] else 0
        for i in (0, 1):
            if decks[i]['track'] is None:
                return i
        return 1
    def _load_entry_on(self, entry, deck):
        """Load one set-list entry's file onto ONE deck (pass
        32 refactor): the single door every row load walks
        through — double-click (via the standby deck), an
        actual row, and the DDJ-RB LOAD buttons (their named
        deck) — so the journal, the analysis and the claims
        treat them identically. Entries from the phone page
        may carry no local file at all — that is said in the
        status bar, never silently ignored."""
        path = entry.get('path')
        if not path or not Path(path).is_file():
            self._status_fn = lambda lang: \
                i18n.tr('setlist_entry_no_file', lang)
            self._apply_status()
            return False
        self.load(deck, path)
        title = entry.get('title') or Path(path).stem
        self._status_fn = lambda lang, t=title, d='AB'[deck]: \
            i18n.tr('setlist_entry_loaded', lang).format(title=t, deck=d)
        self._apply_status()
        return True

    def load_setlist_entry(self, index):
        """Double-click on a set-list row (pass 24): load the
        entry's local file onto the standby deck."""
        entries = (self.setlist_data or {}).get('tracks') or []
        if not 0 <= index < len(entries):
            return
        self._load_entry_on(entries[index], self.standby_deck())
    def _arm_setlist_drops(self):
        """Arm the set-list panel and EVERY descendant for drops
        (pass 25). Two pass-24 gaps this closes: the panel never
        called setAcceptDrops(True), so a real Windows drag was
        never delivered to it at all; and delivery to a child
        under the cursor was left to propagation. Now every
        widget in the panel accepts drops and wears the shared
        SetlistDropFilter, and the panel highlights while a drag
        hovers. Called after the panel is built and at the end
        of every render_setlist (rows are rebuilt there)."""
        panel = getattr(self, 'setlist_panel', None)
        if panel is None:
            return
        drop_filter = getattr(self, '_setlist_drop_filter', None)
        if drop_filter is None:
            drop_filter = SetlistDropFilter(
                panel, self.add_dropped_to_setlist)
            self._setlist_drop_filter = drop_filter
        for widget in [panel, *panel.findChildren(QWidget)]:
            widget.setAcceptDrops(True)
            widget.removeEventFilter(drop_filter)
            widget.installEventFilter(drop_filter)
    def add_dropped_to_setlist(self, paths):
        """Files dropped on the set-list panel (pass 24): audio
        files become real set-list entries (tags/filename title,
        measured duration); anything else is reported by name
        in the status bar — never a silent no-op.

        Pass 25: a dropped FOLDER contributes the audio files
        directly inside it (non-recursive — say so in the
        manual), and a drop with no local files at all (a URL
        dragged out of a browser, say) still earns a status
        explanation instead of silence."""
        if not paths:
            self._status_fn = lambda lang: \
                i18n.tr('setlist_drop_no_local', lang)
            self._apply_status()
            return
        added, rejected = [], []
        for raw in paths:
            candidate = Path(raw)
            if candidate.is_dir():
                inside = [p for p in sorted(candidate.iterdir())
                          if p.is_file()
                          and p.suffix.lower() in SETLIST_AUDIO_EXTS]
                if inside:
                    added.extend(file_track_entry(p) for p in inside)
                else:
                    rejected.append(candidate.name)
            elif candidate.suffix.lower() in SETLIST_AUDIO_EXTS \
                    and candidate.is_file():
                added.append(file_track_entry(candidate))
            else:
                rejected.append(candidate.name)
        if added:
            born = self.setlist_data is None
            if born:
                self.setlist_data = {
                    'format': 'mixcraft-setlist', 'version': 1,
                    'exported_at': None, 'set_bpm': None,
                    'target_minutes': None, 'started': False,
                    'current_index': -1, 'tracks': []}
                # Pass 27: a list born from drops starts with a
                # clean live state too (appends to an EXISTING
                # list keep theirs — row indexes do not move).
                self._reset_setlist_live()
            self.setlist_data['tracks'].extend(added)
            self.clip_tracks = setlist_to_clip_tracks(self.setlist_data)
            if born:
                # Pass 32: a list born from a drop starts with
                # the browse cursor on its first row (Rule C).
                self._cursor_to_first()
            self.render_setlist()
            # Pass 32, Rule A: the dropped songs fill the
            # empty decks (see _autofill_empty_decks).
            self._autofill_empty_decks(added)
        if added or rejected:
            def status_fn(lang, a=len(added), r=tuple(rejected)):
                parts = []
                if a:
                    parts.append(i18n.tr('setlist_drop_added', lang)
                                 .format(count=a))
                if r:
                    parts.append(i18n.tr('setlist_drop_rejected', lang)
                                 .format(names=', '.join(r)))
                return ' • '.join(parts)
            self._status_fn = status_fn
            self._apply_status()
    def _autofill_empty_decks(self, entries):
        """Pass 32, Rule A (user 2026-11-28: 「デッキに曲がない
        状態なら1曲目はAデッキに、2曲目はBデッキに入るはず」):
        entries just dropped onto the set list fill the EMPTY
        decks in A-then-B order, in drop order, through the
        same Window.load the double-click uses — so the
        journal, the analysis and the set-list claims all
        follow from the one path. A deck counts as empty when
        nothing is loaded on it and no load is in flight (a
        load this very batch started counts as spoken for).
        With both decks occupied the drop only adds rows, as
        before. Opening a set-list FILE does not do this —
        open_setlist never auto-loaded, and that stays."""
        taken = set()
        for entry in entries:
            path = entry.get('path')
            if not path:
                continue
            for deck in (0, 1):
                if deck in taken or deck in self.loading:
                    continue
                if self.engine.decks[deck]['track'] is not None:
                    continue
                self.load(deck, path)
                taken.add(deck)
                break

    # -- Pass 27: set list <-> performance linkage ------------------
    # One source of truth: the engine's decks. Every load route
    # (set-list double-click, deck 読込 / file dialog, deck drag &
    # drop, DDJ-RB) ends at the same engine state, so the rows are
    # derived from that state alone — which entry each deck speaks
    # for (its claim) plus the ledger of rows that have sounded.
    # The loaded file's started / current_index / per-track status
    # snapshot is never consulted for the panel any more: it froze
    # the rows at the phone page's last export while the decks
    # played something else, and the ON AIR chips (driven live
    # from the same engine state in tick) contradicted them.
    def _reset_setlist_live(self):
        self._setlist_played = set()
        self._deck_claims = [None, None]
        self._actual_rows = []
        self._actual_played = set()
        self._actual_claims = [None, None]
        self._setlist_sig = None
        # Pass 32: the browse cursor (Rule C) belongs to the
        # list, so a fresh list forgets it; the loader then
        # places it on the first row.
        self._setlist_cursor = None

    def _claim_for(self, deck, path, tracks):
        """The row a deck speaks for when it starts playing
        `path`: the first matching row that has not sounded yet
        and is not claimed by the other deck (rows are played in
        list order when the same file appears twice); if every
        match has sounded, the first unclaimed match (a reprise);
        no match at all -> None (off-list file: no row may light
        up for it). Matching is _path_matches — the same identity
        the loader and the monitor marker use."""
        claimed = {c for j, c in enumerate(self._deck_claims)
                   if j != deck and c is not None}
        first = None
        for idx, entry in enumerate(tracks):
            if idx in claimed \
                    or not _path_matches(entry.get('path'), path):
                continue
            if idx not in self._setlist_played:
                return idx
            if first is None:
                first = idx
        return first

    def _update_setlist_claims(self):
        """Keep the claims in step with the engine (called from
        tick). A claim is chosen when a deck is playing a file
        that matches a row; it sticks while that file stays
        loaded (pause keeps it, resume re-lights the same row)
        and clears when another file replaces it or the deck
        empties. A sounding claim joins the played ledger at
        once, so stopping — button, pause or track end — leaves
        the row 'played'."""
        tracks = (self.setlist_data or {}).get('tracks') or []
        for i, d in enumerate(self.engine.decks):
            track = d['track']
            path = getattr(track, 'path', None)
            claim = self._deck_claims[i]
            if claim is not None and (
                    path is None or claim >= len(tracks)
                    or not _path_matches(tracks[claim].get('path'),
                                         path)):
                claim = None
            if path is not None and d['playing']:
                if claim is None:
                    claim = self._claim_for(i, path, tracks)
                if claim is not None:
                    self._setlist_played.add(claim)
            self._deck_claims[i] = claim
            # Pass 29: the actual-row side of the same tick. A
            # loaded file that matches NO planned row appears as
            # an actual row (created on load, not just on play —
            # the contradiction he photographed was about loaded
            # decks); a listed file never gets one, its claim
            # above is the whole story. The row's deck follows
            # the deck that holds it, and sounding joins the
            # actual ledger, so stop / track end leaves it 済.
            actual = self._actual_claims[i]
            if actual is not None and (
                    path is None or actual >= len(self._actual_rows)
                    or not _path_matches(
                        self._actual_rows[actual].get('path'), path)):
                actual = None
            if path is not None and self.setlist_data is not None \
                    and not any(_path_matches(e.get('path'), path)
                                for e in tracks):
                if actual is None:
                    actual = self._actual_row_for(i, path)
                    if actual is not None:
                        # Pass 32, Rule B (user 2026-11-28: a
                        # song dropped / loaded onto a deck goes
                        # to the TOP of the list): a fresh claim
                        # means this deck just took the file, so
                        # its actual row becomes the topmost —
                        # actual rows are ordered most-recent
                        # deck load first. Every load route ends
                        # here (deck drop, 読込 dialog, double-
                        # click, DDJ-RB LOAD), so they all agree.
                        actual = self._actual_to_top(actual)
                if actual is not None:
                    self._actual_rows[actual]['deck'] = i
                    if d['playing']:
                        self._actual_played.add(actual)
            self._actual_claims[i] = actual

    def _actual_row_for(self, deck, path):
        """The actual row for an off-list file: the existing one
        for that path, or a new one appended in load order. The
        entry is the panel-drop builder's (tags / filename
        title, measured duration); BPM / key come from the
        deck's own analysis, which a bare file read cannot
        know."""
        for idx, entry in enumerate(self._actual_rows):
            if _path_matches(entry.get('path'), path):
                return idx
        try:
            entry = file_track_entry(Path(path))
        except Exception:
            return None
        track = self.decks[deck].wave.track
        if track is not None:
            if entry.get('bpm') is None \
                    and getattr(track, 'bpm', None):
                entry['bpm'] = track.bpm
            if not entry.get('key'):
                entry['key'] = getattr(track, 'camelot', None) \
                    or getattr(track, 'key', None)
        entry['deck'] = deck
        self._actual_rows.append(entry)
        return len(self._actual_rows) - 1

    def _actual_to_top(self, idx):
        """Move an actual row to the top of the actual section
        (pass 32, Rule B), remapping the index-based live
        state — the played ledger and both decks' claims — so
        every index keeps pointing at its own row."""
        if idx <= 0:
            return idx

        def remap(j):
            if j == idx:
                return 0
            return j + 1 if j < idx else j

        row = self._actual_rows.pop(idx)
        self._actual_rows.insert(0, row)
        self._actual_played = {remap(j) for j in self._actual_played}
        self._actual_claims = [
            remap(c) if c is not None else None
            for c in self._actual_claims]
        return 0

    def _actual_live_statuses(self):
        """Live statuses for the actual rows alone — the same
        pure rule as the planned rows (pass 27), computed over
        their own claims / ledger so the two groups never
        disturb each other's next-marker."""
        playing = {self._actual_claims[i]
                   for i, d in enumerate(self.engine.decks)
                   if d['playing']
                   and self._actual_claims[i] is not None}
        return live_statuses(self._actual_rows, playing,
                             self._actual_played)

    def _load_actual_entry(self, index):
        """Double-click on an actual row (pass 29): the same
        standby-deck load as a planned row, from the actual
        row's own file."""
        if not 0 <= index < len(self._actual_rows):
            return
        self._load_entry_on(self._actual_rows[index],
                            self.standby_deck())

    # -- Pass 32, Rule C: the browse cursor ----------------------
    # One selection shared by the mouse (a click on a row) and
    # the DDJ-RB (BROWSE turn walks it, LOAD 1/2 load from it) —
    # rekordbox's browser semantics. A cursor identity is
    # ('actual', path) for an actual row — the path survives
    # the Rule-B reordering — or ('planned', index) for a
    # planned row, whose index never moves.
    def _setlist_identities(self):
        """Every rendered row's cursor identity, in display
        order (actual rows above planned rows)."""
        ids = [('actual', row.get('path'))
               for row in self._actual_rows]
        tracks = (self.setlist_data or {}).get('tracks') or []
        ids += [('planned', i) for i in range(len(tracks))]
        return ids

    def _cursor_to_first(self):
        ids = self._setlist_identities()
        self._setlist_cursor = ids[0] if ids else None

    def _select_setlist_row(self, section, index):
        """Mouse click on a row: the cursor goes there (the
        double-click that may follow still loads, unchanged)."""
        if section == 'actual':
            if not 0 <= index < len(self._actual_rows):
                return
            self._setlist_cursor = (
                'actual', self._actual_rows[index].get('path'))
        else:
            tracks = (self.setlist_data or {}).get('tracks') or []
            if not 0 <= index < len(tracks):
                return
            self._setlist_cursor = ('planned', index)
        self.render_setlist()

    def move_setlist_cursor(self, steps):
        """BROWSE turn: walk the cursor `steps` rows (the
        knob's difference-count magnitude), clamped at both
        ends — no wrap, like rekordbox's browser."""
        ids = self._setlist_identities()
        if not ids or not steps:
            return
        try:
            pos = ids.index(self._setlist_cursor)
        except ValueError:
            pos = 0
        new = min(len(ids) - 1, max(0, pos + steps))
        if ids[new] != self._setlist_cursor:
            self._setlist_cursor = ids[new]
            self.render_setlist()

    def load_cursor_row(self, deck):
        """LOAD 1 / LOAD 2: load the cursor row's file onto
        the NAMED deck, through the same door as a double-
        click — a file-less row explains itself in the status
        bar with the same words."""
        ids = self._setlist_identities()
        if not ids:
            return
        ident = self._setlist_cursor \
            if self._setlist_cursor in ids else ids[0]
        section, key = ident
        if section == 'actual':
            entry = next((row for row in self._actual_rows
                          if row.get('path') == key), None)
            if entry is None:
                return
        else:
            entry = ((self.setlist_data or {}).get('tracks')
                     or [])[key]
        self._load_entry_on(entry, deck)

    def _setlist_live_statuses(self):
        tracks = (self.setlist_data or {}).get('tracks') or []
        playing = {self._deck_claims[i]
                   for i, d in enumerate(self.engine.decks)
                   if d['playing']
                   and self._deck_claims[i] is not None}
        return live_statuses(tracks, playing, self._setlist_played)

    def _monitor_paths(self):
        """The loaded-track paths of every monitored deck
        (pass 28: both decks may be monitored at once, so the
        set-list marker is a SET of paths, not one)."""
        paths = set()
        for i in self.monitor_decks:
            track = self.decks[i].wave.track
            if track is not None:
                path = getattr(track, 'path', None)
                if path:
                    paths.add(path)
        return frozenset(paths)

    def _setlist_signature(self):
        """What the rendered rows depend on: the live statuses
        (planned and, pass 29, actual), the actual rows
        themselves, plus the monitor row markers' target paths."""
        return (tuple(self._setlist_live_statuses()),
                tuple(self._actual_live_statuses()),
                tuple((r.get('path'), r.get('deck'))
                      for r in self._actual_rows),
                self._monitor_paths(),
                # Pass 32: the browse cursor's frame is painted
                # from the same render, so it joins the signature.
                self._setlist_cursor)

    def _refresh_setlist_live(self):
        """Re-render the rows only when the live signature moved
        (tick calls this every cycle; the rows are rebuilt only
        on a real change, so nothing flickers)."""
        if self.setlist_data is None:
            return
        if self._setlist_signature() != self._setlist_sig:
            self.render_setlist()

    def render_setlist(self):
        while self.setlist_rows.count():
            item = self.setlist_rows.takeAt(0)
            widget = item.widget()
            if widget: widget.deleteLater()
        if self.setlist_data is None:
            self._arm_setlist_drops()
            return
        # Pass 27: rendering records the signature it painted, so
        # tick's change detection compares like with like.
        self._setlist_sig = self._setlist_signature()
        # Pass 13: no summary line is composed here any more (see
        # the panel build) — render_setlist only renders the rows.
        # Pass 15: a monitored deck's loaded track, when it is in
        # the list, wears the hollow MONITOR marker on its row.
        # Pass 28: EVERY monitored deck's track wears it (both
        # decks may be monitored at once).
        monitor_paths = self._monitor_paths()
        entries = (self.setlist_data or {}).get('tracks') or []
        # Pass 27: the rows' statuses are the LIVE ones (decks'
        # real state via the claims/ledger), not the file's
        # snapshot — display_rows uses them verbatim.
        # Pass 29: ACTUAL rows (off-list tracks the decks really
        # loaded / played) render ABOVE the planned rows, in the
        # order the decks took them, with their real deck letter
        # instead of the plan's alternation; planned rows follow
        # with their pass-27 statuses and alternation untouched.
        items = []
        if self._actual_rows:
            for row, entry in zip(
                    display_rows({'format': 'mixcraft-setlist',
                                  'version': 1,
                                  'tracks': self._actual_rows},
                                 statuses=self._actual_live_statuses()),
                    self._actual_rows):
                items.append((row, entry,
                              'AB'[entry.get('deck') or 0],
                              self._load_actual_entry,
                              lambda idx: self._select_setlist_row(
                                  'actual', idx),
                              ('actual', entry.get('path'))))
        for row in display_rows(
                self.setlist_data,
                statuses=self._setlist_live_statuses()):
            entry = entries[row['index']] \
                if row['index'] < len(entries) else {}
            # Pass 31: a row imported from a Live set carries
            # the deck its track really was (track 1 = A,
            # track 2 = B) — the letter follows the entry, not
            # the alternation. Phone lists carry no deck and
            # keep the alternation untouched.
            entry_deck = entry.get('deck')
            items.append((row, entry,
                          'AB'[entry_deck] if entry_deck in (0, 1)
                          else 'A' if row['number'] % 2 == 1 else 'B',
                          self.load_setlist_entry,
                          lambda idx: self._select_setlist_row(
                              'planned', idx),
                          ('planned', row['index'])))
        for row_index, (row, entry, deck_letter, activate,
                        select, identity) in enumerate(items):
            # Pass 14: the rows live on old-LCD glass now, and an old
            # LCD has no colour — the fixed status colours become
            # INK DENSITIES plus the text badges: played = dim ink,
            # queued = mid-density bar, next = full ink, and the
            # NOW PLAYING row is INVERTED (ink background, glass-pale
            # text), the way old LCDs highlight a selection. The
            # status semantics are unchanged; only their LCD rendering
            # is. (The COLOR_STATE_* constants keep the phone
            # design's colours on record; they no longer paint here.)
            if row['status'] == 'playing':
                fg, fg_soft = COLOR_LCD_PALE, COLOR_LCD_PALE_DIM
                bar_color, chip_color = COLOR_LCD_PALE, COLOR_LCD_PALE
            elif row['status'] == 'played':
                fg = fg_soft = COLOR_LCD_INK_DIM
                bar_color, chip_color = '#8a9678', COLOR_LCD_INK_DIM
            elif row['status'] == 'next':
                fg, fg_soft = COLOR_LCD_INK, COLOR_LCD_INK_SOFT
                bar_color, chip_color = COLOR_LCD_INK, COLOR_LCD_INK_SOFT
            else:  # queued
                fg, fg_soft = COLOR_LCD_INK, COLOR_LCD_INK_SOFT
                bar_color, chip_color = '#5c6651', COLOR_LCD_INK_SOFT
            # Deck indicator (pass 6, user direction 2026-11-26):
            # the letter chip says which deck a track belongs to,
            # following the established alternation (live_plan.py):
            # odd positions (1,3,5...) -> Deck A, even (2,4,6...) ->
            # Deck B. Pass 15: the bar beside it is NEUTRAL ink now —
            # the A=blue / B=red colour coding is retired, the
            # letter carries the identity. Pass 29: for ACTUAL rows
            # the letter is the deck that really holds the track
            # (carried in the item tuple), not the alternation.
            line = QHBoxLayout()
            line.setContentsMargins(6, 3, 6, 3)
            # Pass 30 (user 2026-11-28: 「リストの方は再生中の曲が
            # わかるようにしてほしい」): the now-playing signal
            # also lives at the row's LEFT edge — a FILLED chip —
            # not only as text in the rightmost badge column,
            # which his 150% scaling clips off the panel (his
            # screenshot showed only 「待」 of the status column).
            # On the inverted row the fill is the glass-pale ink
            # with ink letters, so it reads as a lit lamp on the
            # ink block; other rows keep an empty slot of the
            # same width so the columns stay aligned.
            now_chip = QLabel(
                i18n.status_badge('playing', self.lang)
                if row['status'] == 'playing' else '')
            now_chip.setFixedWidth(48)
            now_chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
            if row['status'] == 'playing':
                now_chip.setStyleSheet(
                    f'color:{COLOR_LCD_INK};background:{COLOR_LCD_PALE};'
                    f'border:1px solid {COLOR_LCD_PALE};'
                    f'border-radius:2px;font-family:{MONO_STACK};'
                    'font-size:10px;font-weight:800')
            line.addWidget(now_chip)
            deck_bar = QFrame()
            deck_bar.setFixedWidth(5)
            deck_bar.setStyleSheet(f'background:{COLOR_LCD_INK_DIM};border:none')
            line.addWidget(deck_bar)
            chip = QLabel(deck_letter)
            chip.setFixedWidth(22)
            chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
            chip.setStyleSheet(f'color:{chip_color};background:transparent;border:1px solid {chip_color};border-radius:2px;font-size:11px;font-weight:800')
            line.addWidget(chip)
            bar = QFrame()
            bar.setFixedWidth(6)
            bar.setStyleSheet(f'background:{bar_color};border:none')
            line.addWidget(bar)
            number = QLabel(f'{row["number"]:02d}')
            number.setStyleSheet(f'color:{fg};font-family:{MONO_STACK};font-size:14px;font-weight:700')
            number.setFixedWidth(34)
            line.addWidget(number)
            title_text = row['title'] or i18n.tr('untitled', self.lang)
            if row['artist']: title_text += f' — {row["artist"]}'
            title = QLabel(title_text)
            title.setStyleSheet(f'color:{fg};font-size:13px;font-weight:600')
            title.setWordWrap(True)
            line.addWidget(title, 1)
            meta_qss = f'color:{fg_soft};font-family:{MONO_STACK};font-size:12px'
            bpm = QLabel(f'{row["bpm"]:g}' if row['bpm'] is not None else '—')
            bpm.setStyleSheet(meta_qss)
            bpm.setFixedWidth(64)
            bpm.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            line.addWidget(bpm)
            key = QLabel(row['key'] or '—')
            key.setStyleSheet(meta_qss)
            key.setFixedWidth(56)
            key.setAlignment(Qt.AlignmentFlag.AlignCenter)
            line.addWidget(key)
            duration = QLabel(row['duration_text'])
            duration.setStyleSheet(meta_qss)
            duration.setFixedWidth(52)
            duration.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            line.addWidget(duration)
            # Pass 15: the monitor marker — the same hollow outline
            # style as the deck strip's MONITOR chip, on the row of
            # the monitored deck's loaded track (matched by path,
            # pass 13's rule). Other rows keep an empty slot of the
            # same width so the badge column stays aligned.
            is_monitored = any(
                _path_matches(entry.get('path'), monitored_path)
                for monitored_path in monitor_paths)
            mon = QLabel(i18n.tr('monitor_short', self.lang)
                         if is_monitored else '')
            mon.setFixedWidth(58)
            mon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            if is_monitored:
                mon.setStyleSheet(f'color:{fg};background:transparent;border:1px solid {fg};border-radius:2px;font-family:{MONO_STACK};font-size:10px;font-weight:700')
            line.addWidget(mon)
            badge = QLabel(i18n.status_badge(row['status'], self.lang))
            badge.setStyleSheet(f'color:{fg};font-family:{MONO_STACK};font-size:11px;font-weight:700')
            badge.setFixedWidth(84)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            line.addWidget(badge)
            # Pass 13: the row ends at the status badge — the old
            # 70px link slot rendered as an empty dark box on every
            # row that had no link, which is exactly the clutter the
            # user named. The URL stays in the set-list data; only
            # the slot (and its rare LISTEN button) leaves the row.
            # Pass 14: the rows are flat lines on the glass with a
            # hairline under each (see the STYLE rules), and the
            # NOW PLAYING row's container is the inverted ink block.
            # Pass 24: the row is a SetlistRow — a double-click
            # loads the entry's file onto the standby deck.
            # Pass 29: actual rows activate from their own list.
            # Pass 32: a click selects the row (the browse
            # cursor), and the cursor row wears the amber frame
            # (the [browse_cursor="true"] STYLE rules) — an outline,
            # not a fill, so it composes with the playing row's
            # inversion and the pass-30 chip instead of
            # fighting them.
            container = SetlistRow(row['index'], activate, select)
            if row['status'] == 'playing':
                container.setObjectName('setrowplaying')
            else:
                container.setObjectName(f'setrow{row_index % 2}')
            if identity == self._setlist_cursor:
                container.setProperty('browse_cursor', True)
            container.setToolTip(i18n.tr('setlist_row_tip', self.lang))
            container.setLayout(line)
            self.setlist_rows.addWidget(container)
        # Pass 25: the rows (and their labels) were just rebuilt —
        # re-arm the whole panel for drops.
        self._arm_setlist_drops()
    def tick(self):
        # Pass 21: with no live audio stream there is no callback
        # to apply the engine's command queue, and unapplied
        # commands mint no journal events — pump the queue here
        # so the performance capture survives a dead device.
        if self.stream is None:
            self.engine.apply_pending()
        try: self.journal.drain(self.engine)
        except OSError as e: self.status.setText(f'Autosave error: {e}')
        for widget,d in zip(self.decks,self.engine.decks):
            for button, key in [(widget.quant,'quantize'),(widget.sync,'sync'),(widget.key_lock,'key_lock')]:
                button.blockSignals(True); button.setChecked(d[key]); button.blockSignals(False)
            # Pass 15: the ON AIR chip + heavy LCD frames follow the
            # engine's real per-deck playing state, live — play,
            # pause, stop and track-end all land here within a tick.
            widget.set_onair(d['playing'])
            # Pass 33: the KEY LOCK chip's fill follows the engine
            # state the same way (its checked state was just synced
            # above, under blocked signals).
            widget.set_key_lock_chip(d['key_lock'])
            if widget.wave.playing != d['playing']:
                widget.wave.playing = d['playing']
                widget.wave.update()
            # Pass 7: repaint the (scrolling) waveform only when the
            # position actually moved or the loaded track changed —
            # no busy repaint while a deck sits paused.
            if (widget.wave.position != d['position']
                    or widget.wave.track is not d['track']):
                widget.wave.position = d['position']
                widget.wave.update()
            widget.play.setText(i18n.tr('pause' if d['playing'] else 'play', self.lang))
            def clock(s):
                s = max(0.0, s)
                return f'{int(s)//60:02}:{s%60:04.1f}'
            # Pass 20 (user review fix, 2026-11-27: 「残り時間はほ
            # しいね」): the DECK clock shows the remaining +
            # elapsed pair again ('-00:11.0  00:13.0'), undoing
            # pass 16 for the decks only — the MIX TIMER further
            # down stays elapsed-only, as pass 16 left it. With no
            # track loaded there is no remaining to show, so the
            # clock falls back to elapsed alone.
            elapsed = d['position'] / RATE
            if d['track'] is not None:
                remaining = max(0.0, len(d['track'].audio) / RATE - elapsed)
                widget.clock.setText(f'-{clock(remaining)}  {clock(elapsed)}')
            else:
                widget.clock.setText(clock(elapsed))
        # Pass 35: the AUTO SYNC chip follows the engine's mode
        # — a SYNC pad press may have switched it off.
        if self.auto_sync_chip.isChecked() != self.engine.auto_sync:
            self.auto_sync_chip.blockSignals(True)
            self.auto_sync_chip.setChecked(self.engine.auto_sync)
            self.auto_sync_chip.blockSignals(False)
        self.set_auto_sync_chip(self.engine.auto_sync)
        # Pass 26: when a SYNC engagement lands as tempo-only (a
        # beat grid was not confident), the status bar says so —
        # once per engagement, on the transition, not every tick.
        # Pass 35: the same watcher carries the bar outcome of
        # an AUTO SYNC engage — beat phase aligned but a track
        # had no bar information, so the bars were NOT aligned
        # and the status bar says that too.
        for i, d in enumerate(self.engine.decks):
            state = (d.get('sync_mode'), d.get('sync_bar'))
            if state != self._sync_mode_seen[i]:
                if d['sync'] and state[0] == 'tempo':
                    self._status_fn = lambda lang: \
                        i18n.tr('sync_tempo_only', lang)
                    self._apply_status()
                elif d['sync'] and state[0] == 'phase' \
                        and state[1] is False:
                    self._status_fn = lambda lang: \
                        i18n.tr('sync_no_bar_info', lang)
                    self._apply_status()
                self._sync_mode_seen[i] = state
        # Pass 27: the set list follows the decks — claims update
        # from the engine's real state and the rows re-render on
        # change, in the same tick that drives the ON AIR chips
        # from that state, so the two can never disagree. Frozen
        # during the transition audition: the audition drives both
        # decks' playing flags with rehearsal material, and an
        # 8-second rehearsal must not mark rows played.
        if self.engine.preview is None:
            self._update_setlist_claims()
            self._refresh_setlist_live()
        # Pass 8: drive the mix timer off this same tick — auto-start
        # on the first deck play (audition playback counts), then
        # accumulate from monotonic deltas and refresh the readouts.
        now = time.monotonic()
        mix_timer_auto_start(self.mix_timer,
                             any(d['playing'] for d in self.engine.decks), now)
        mix_timer_update(self.mix_timer, now)
        self.refresh_mix_timer()
        self.meter.setValue(int(self.engine.peak*100))
        self.meter.setToolTip(f'Limiter: {self.engine.overloads} / Audio status: {self.audio_status_count}')
        self.cross.setEnabled(self.engine.preview is None)
        self.preview_button.setEnabled(self.engine.preview is None)
        # Pass 23: DDJ-RB — drain hardware messages into the UI,
        # retry the connection, refresh the PLAY / monitor LEDs.
        self.midi.poll()
        # Pass 25: if the sound is not on the DDJ-RB yet, keep
        # re-trying the ladder (device appeared / MIDI connected).
        self._maybe_retry_audio(now)
    def showEvent(self, event):
        super().showEvent(event)
        if not self._fit_done:
            self._fit_done = True
            self._fit_frame_to_screen()
    def _fit_frame_to_screen(self):
        """Pass 24: fit the window's FRAME (title bar + borders
        included) inside the screen's available geometry. resize()
        sets the client size, so the pass-23 clamp alone left the
        frame sticking out past the work area by the frame's size.
        Runs once, at first show, when frameGeometry() is real;
        on frame-less platforms it is a no-op."""
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        frame = self.frameGeometry()
        extra_w = frame.width() - self.width()
        extra_h = frame.height() - self.height()
        new_w = min(self.width(), max(200, avail.width() - extra_w))
        new_h = min(self.height(), max(200, avail.height() - extra_h))
        if (new_w, new_h) != (self.width(), self.height()):
            self.resize(new_w, new_h)
            frame = self.frameGeometry()
        x = min(max(frame.x(), avail.left()),
                max(avail.left(), avail.right() - frame.width() + 1))
        y = min(max(frame.y(), avail.top()),
                max(avail.top(), avail.bottom() - frame.height() + 1))
        if (x, y) != (frame.x(), frame.y()):
            self.move(x, y)
    def closeEvent(self,event):
        self.timer.stop()
        self.midi.close()
        self.pool.shutdown(wait=True)
        self._close_streams()
        self.journal.drain(self.engine)
        self.journal.file.close()
        event.accept()

STYLE = (
    # Global dark theme — every stock widget is covered so nothing
    # renders in the light system palette (the first pass missed the
    # app-level stylesheet in some paths; this is the single source).
    f'QWidget {{ background:{COLOR_BG}; color:{COLOR_TEXT}; font-family:{FONT_STACK}; font-size:13px; }}'
    f'QMainWindow {{ background:{COLOR_BG}; }}'
    f'QScrollArea {{ background:{COLOR_BG}; border:none; }}'
    f'QLabel {{ background:transparent; }}'
    # Pass 9 material: panels are machined surfaces, not flat
    # rectangles — a 1px top-edge highlight (the bright first stop),
    # a subtle vertical falloff, and consistent 6px corners.
    f'QFrame#headerbar {{ background:qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #202127, stop:0.05 #131418, stop:1 #0e0e11); border:1px solid {COLOR_BORDER}; border-radius:3px; }}'
    f'QFrame#deck {{ background:qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #2b2d35, stop:0.004 #1a1b20, stop:0.5 #121317, stop:1 #0e0f11); border:1px solid #26282f; border-radius:3px; padding:4px 8px; }}'
    f'QFrame#mixer, QFrame#bpmframe {{ background:qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #2b2d35, stop:0.004 #1a1b20, stop:0.5 #131418, stop:1 #0f1013); border:1px solid #26282f; border-radius:3px; padding:4px 12px; }}'
    f'QFrame#setlist {{ background:qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #2b2d35, stop:0.004 #1a1b20, stop:0.4 #121317, stop:1 #0f1013); border:1px solid #26282f; border-radius:3px; padding:4px 12px; }}'
    # Pass 25: the set-list panel's live-drop highlight — while a
    # file drag hovers, the frame turns the muted amber of the
    # MASTER / GAIN / FILTER accents, so the target reads as live.
    f'QFrame#setlist[drop_active="true"] {{ border:2px solid {COLOR_KNOB_ACCENT}; }}'
    f'QFrame#utility {{ background:qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #24262d, stop:0.005 #15161a, stop:1 #101114); border:1px solid #26282f; border-radius:3px; }}'
    # Pass 14: the LCD windows — pale grey-green glass with a dark
    # bezel, set into the chassis panels (the ghost grid + inset
    # shading are painted over this by LcdWindow / the widgets).
    f'QFrame#lcdinfo, QFrame#lcdlist {{ background:{LCD_GLASS_GRADIENT}; border:1px solid #131709; border-radius:3px; }}'
    # Pass 15: the playing deck's info window — heavy ink frame.
    f'QFrame#lcdinfo[heavy="true"] {{ border:3px solid {COLOR_LCD_INK}; }}'
    # Pass 14: set-list rows are flat lines ON the glass — a whisper
    # of striping and a hairline under each row; the NOW PLAYING row
    # is the inverted ink block (its labels wear the pale styles
    # render_setlist gives them).
    f'QFrame#setrow0 {{ background:rgba(34,43,28,0.05); border:2px solid transparent; border-bottom:1px solid rgba(34,43,28,0.25); border-radius:0px; }}'
    f'QFrame#setrow1 {{ background:transparent; border:2px solid transparent; border-bottom:1px solid rgba(34,43,28,0.25); border-radius:0px; }}'
    f'QFrame#setrowplaying {{ background:{COLOR_LCD_INK}; border:2px solid transparent; border-bottom:1px solid {COLOR_LCD_INK}; border-radius:0px; }}'
    # Pass 32: the browse cursor's frame — every row always
    # carries a 2px transparent border (above) so the amber
    # outline (the app's one attention colour, the drop
    # highlight's) can land without shifting the layout by a
    # pixel. An outline only: it never recolours the row, so
    # the playing row's inversion and chip stay legible under
    # it.
    f'QFrame#setrow0[browse_cursor="true"], QFrame#setrow1[browse_cursor="true"], QFrame#setrowplaying[browse_cursor="true"] {{ border-color:{COLOR_KNOB_ACCENT}; }}'
    f'QPushButton {{ background:qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1a1b20, stop:1 #121317); border:1px solid #2c2e36; border-radius:3px; padding:6px 10px; font-weight:600; color:{COLOR_TEXT}; }}'
    f'QPushButton:hover {{ background:qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #282a31, stop:1 #17181d); border-color:#3d404a; }}'
    f'QPushButton:pressed {{ background:#0a0a0d; border-color:#1d1f24; }}'
    f'QPushButton:disabled {{ background:#121317; border-color:#1d1f24; color:#565b63; }}'
    f'QPushButton:checked {{ background:qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #2e3138, stop:1 #1a1b20); border-color:{COLOR_BORDER_HI}; color:{COLOR_TEXT}; font-weight:800; }}'
    # Pass 24: the deck transport stack wears slimmer vertical
    # padding — its minimum height is part of the window's floor
    # on short screens.
    f'QPushButton[transport="true"] {{ padding:3px 10px; }}'
    # Pass 24: same for the utility bar's buttons and the device
    # combo — set-up controls, not performance surfaces.
    f'QPushButton[util="true"] {{ padding:3px 10px; }}'
    f'QComboBox[util="true"] {{ padding:3px 9px; }}'
    # Header language toggle (pass 10): same treatment as the normal
    # buttons but slimmer — the header bar keeps its pass-9 height,
    # so the bottom notice stays inside the 900px window.
    f'QPushButton#langbtn {{ background:qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1a1b20, stop:1 #121317); border:1px solid #2c2e36; border-radius:3px; padding:2px 10px; font-weight:600; color:{COLOR_TEXT}; }}'
    f'QPushButton#langbtn:hover {{ background:qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #282a31, stop:1 #17181d); border-color:#3d404a; }}'
    f'QPushButton#langbtn:pressed {{ background:#0a0a0d; border-color:#1d1f24; }}'
    f'QPushButton#langbtn:checked {{ background:qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #2e3138, stop:1 #1a1b20); border-color:{COLOR_BORDER_HI}; color:{COLOR_TEXT}; font-weight:800; }}'
    # Performance pads: near-black cells, hairline borders, small caps.
    f'QPushButton[pad="true"] {{ background:qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #14151a, stop:1 #0a0a0c); border:1px solid #26282f; border-radius:2px; padding:2px 4px; font-size:11px; font-weight:700; color:#c9ccd1; }}'
    f'QPushButton[pad="true"]:hover {{ background:qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1a1b21, stop:1 #0e0f12); border-color:#3f434e; }}'
    f'QPushButton[pad="true"]:pressed {{ background:#060607; }}'
    f'QPushButton[pad="true"]:checked {{ background:qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #2e3138, stop:1 #1a1b20); border-color:{COLOR_BORDER_HI}; color:{COLOR_TEXT}; }}'
    f'QComboBox {{ background:#17181d; border:1px solid #2a2c33; border-radius:3px; padding:6px 9px; color:{COLOR_TEXT}; }}'
    f'QComboBox:hover {{ border-color:#3a3d46; }}'
    f'QComboBox::drop-down {{ border:none; width:18px; }}'
    f'QComboBox QAbstractItemView {{ background:#17181d; color:{COLOR_TEXT}; border:1px solid #2a2c33; selection-background-color:#0a2e4d; }}'
    f'QSlider::groove:horizontal {{ height:8px; background:#060607; border:1px solid {COLOR_BORDER}; border-radius:2px; }}'
    f'QSlider::sub-page:horizontal {{ background:#3a3d46; border-radius:2px; }}'
    f'QSlider::handle:horizontal {{ width:14px; height:24px; margin:-9px 0; background:qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #d8d8d8, stop:0.44 #d8d8d8, stop:0.5 #1c1d1f, stop:0.56 #d8d8d8, stop:1 #d8d8d8); border:2px solid #000000; border-radius:2px; }}'
    f'QProgressBar {{ background:{COLOR_DISPLAY_BG}; border:1px solid {COLOR_BORDER}; border-radius:3px; min-height:15px; color:{COLOR_DISPLAY_FG}; font-family:{MONO_STACK}; }}'
    f'QProgressBar::chunk {{ background:qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1fa34a, stop:0.75 #2ee66b, stop:0.92 #ffc233, stop:1 #ff3838); border-radius:1px; }}'
    f'QLabel#status {{ background:{LCD_GRADIENT}; border:1px solid {COLOR_BORDER}; border-radius:2px; padding:2px 10px; color:{COLOR_DISPLAY_FG}; font-family:{MONO_STACK}; font-size:13px; }}'
    f'QToolTip {{ background:{COLOR_PANEL_HI}; color:{COLOR_TEXT}; border:1px solid {COLOR_BORDER_HI}; padding:4px 6px; }}'
    # Pass 24: when the content truly does not fit, the scroll
    # bars are the way down — they must READ as controls, not
    # hide as 12px of near-black on black (the pass-23 handles
    # were #2c2f36 on the app background; the user read the
    # overflow as content being cut off).
    f'QScrollBar:vertical {{ background:{COLOR_BG}; width:14px; margin:0; }}'
    f'QScrollBar::handle:vertical {{ background:#4b515b; border-radius:3px; min-height:30px; }}'
    f'QScrollBar::handle:vertical:hover {{ background:#6a7280; }}'
    f'QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}'
    f'QScrollBar:horizontal {{ background:{COLOR_BG}; height:14px; margin:0; }}'
    f'QScrollBar::handle:horizontal {{ background:#4b515b; border-radius:3px; min-width:30px; }}'
    f'QScrollBar::handle:horizontal:hover {{ background:#6a7280; }}'
    f'QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width:0; }}'
)

if __name__ == '__main__':
    # Pass 23 (window fit): high-DPI handling before the
    # QApplication exists. Qt 6 scales per-monitor by default; the
    # PassThrough rounding policy keeps fractional scale factors
    # (125% / 150%) from rounding the window's logical size up
    # past the physical screen.
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    # Pass 12: the bundled DotGothic16 becomes the application font;
    # the stylesheet's stacks name it first as well, so widgets the
    # stylesheet does not reach (dialogs, menus) match the window.
    if load_dot_font():
        app_font = app.font()
        app_font.setFamily(DOT_FONT_FAMILY)
        app.setFont(app_font)
    app.setStyleSheet(STYLE)
    smoke_mode = len(sys.argv)>2 and sys.argv[1] in ('--smoke-test','--audio-smoke-test')
    window = Window(audio=not smoke_mode)
    window.show()
    if smoke_mode:
        from smoke import schedule
        schedule(app,window,sys.argv[2],sys.argv[1]=='--audio-smoke-test')
    else:
        for i,path in enumerate(sys.argv[1:3]): window.load(i,path)
    sys.exit(app.exec())
