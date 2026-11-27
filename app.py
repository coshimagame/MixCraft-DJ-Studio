import math
import os
import sys
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import sounddevice as sd
from PySide6.QtCore import Qt, QTimer, Signal, QObject, QUrl, QRectF
from PySide6.QtGui import (QColor, QFont, QLinearGradient, QPainter, QPen,
    QDesktopServices, QRadialGradient)
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QSlider, QFileDialog, QComboBox, QMessageBox, QFrame, QProgressBar, QScrollArea)
from engine import (DEFAULT_TARGET_MINUTES, Engine, Journal, RATE,
                    WAVE_WINDOW_SECONDS, format_mix_clock,
                    format_mix_remaining, frame_to_x, load_track,
                    mix_timer_auto_start, mix_timer_reset, mix_timer_start,
                    mix_timer_stop, mix_timer_update, new_mix_timer,
                    peak_span, scroll_window, seek_fraction)
from audio_config import resolve_output
from setlist import (SetlistFormatError, display_rows, display_summary,
                     load_setlist, setlist_to_clip_tracks)

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
    'deck_a': '#0e9bff',       # deck 1 blue accent (his FX/SYNC blue)
    'deck_b': '#ff4a2d',       # deck 2 red-orange accent (slider fills / chip only)
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
COLOR_DECK_A = THEME['deck_a']   # Deck A accent (blue, rekordbox deck 1)
COLOR_DECK_B = THEME['deck_b']   # Deck B accent (red, rekordbox deck 2)
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
COLOR_WAVE_BG = COLOR_DISPLAY_BG
COLOR_WAVE_GRID = THEME['wave_grid']
# Waveform band colours (rekordbox RGB scheme, estimates — analysis doc §2)
COLOR_WAVEFORM_LOW = '#ff3838'
COLOR_WAVEFORM_MID = '#2ee66b'
COLOR_WAVEFORM_HIGH = '#2e9bff'
FONT_STACK = '"Segoe UI", "Yu Gothic UI", "Meiryo UI", sans-serif'
MONO_STACK = 'Consolas, "DejaVu Sans Mono", "Courier New", monospace'
SECTION_LABEL_QSS = f'color:{COLOR_TEXT_MUTE};font-family:{MONO_STACK};font-size:9px;font-weight:700;letter-spacing:2px'
LCD_GRADIENT = ('qlineargradient(x1:0, y1:0, x2:0, y2:1, '
                'stop:0 #0a0a0c, stop:0.45 #050506, stop:1 #070708)')

def lcd_qss(size, bold=True, fg=None):
    """Dark-glass numeric readout style for display-only labels."""
    return (f'background:{LCD_GRADIENT};color:{fg or COLOR_DISPLAY_FG};'
            f'font-family:{MONO_STACK};font-size:{size}px;letter-spacing:1px;'
            f'font-weight:{700 if bold else 400};'
            f'border:1px solid {COLOR_BORDER};border-radius:3px;padding:3px 10px')

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
    marker) in sight. Both areas draw the same track.peaks data and
    the same blue scheme on both decks. Window/position math lives in
    engine.py (scroll_window / peak_span / seek_fraction), Qt-free and
    unit-tested. Clicking the overview seeks by track fraction;
    clicking the scrolling area seeks to the time shown at that x."""
    seek = Signal(float)
    def __init__(self, color):
        super().__init__()
        self.track = None
        self.position = 0
        self.color = color
        # Pass 9 (sketch layout): the stacked strips give the flowing
        # waveform real height again — the user: 「波形データ大きく
        # てもいいよ」. 100 total (scrolling area + 14 overview).
        self.setMinimumHeight(100)
    def paintEvent(self, event):
        p = QPainter(self)
        # Pass 9: the waveform glass gets a subtle vertical sheen —
        # darker at the centre line, a touch of light at the edges.
        glass = QLinearGradient(0, 0, 0, self.height())
        glass.setColorAt(0.0, QColor('#0a0a0d'))
        glass.setColorAt(0.5, QColor(COLOR_WAVE_BG))
        glass.setColorAt(1.0, QColor('#08080a'))
        p.fillRect(self.rect(), glass)
        w, h = self.width(), self.height()
        main_h = h - WAVE_OVERVIEW_HEIGHT - 1
        mid = main_h/2
        if not self.track:
            p.setPen(QColor(COLOR_TEXT_MUTE))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, 'DROP AUDIO HERE  /  音声ファイルをドロップ')
            return
        total = len(self.track.audio)
        start, end = scroll_window(self.position)
        # One-second grid lines scroll WITH the music, so the flow is
        # visible even in a steady passage.
        p.setPen(QColor(COLOR_WAVE_GRID))
        for s in range(math.ceil(start/RATE), int(end//RATE)+1):
            x = int(frame_to_x(s*RATE, start, end, w))
            p.drawLine(x, 0, x, main_h)
        p.setPen(QColor('#1a1c20'))
        p.drawLine(0, int(mid), w, int(mid))
        # rekordbox blue scheme, pass 9 gradient treatment: each peak
        # is drawn in three passes — a deep-blue base at full height,
        # the body blue at 72%, the bright core at 42% — so the wave
        # reads as lit from inside, the same on both decks. Each peak
        # is a bar as wide as its bucket's span in the window, so
        # long tracks (few buckets in view) still read dense.
        peaks = self.track.peaks
        count = len(peaks)
        bucket_px = (total/count)/(end-start)*w if count else 1
        pen_w = max(1, int(bucket_px))
        base = QPen(QColor('#144a86'), pen_w)
        body = QPen(QColor(self.color), pen_w)
        core = QPen(QColor(COLOR_WAVE_LIGHT), pen_w)
        lo, hi = peak_span(count, total, start, end)
        for i in range(lo, hi):
            x = int(frame_to_x((i+0.5)*total/count, start, end, w))
            y = int(peaks[i]*(mid-6))
            p.setPen(base)
            p.drawLine(x, int(mid)-y, x, int(mid)+y)
            mid_y = int(y*0.72)
            p.setPen(body)
            p.drawLine(x, int(mid)-mid_y, x, int(mid)+mid_y)
            inner = int(y*0.42)
            p.setPen(core)
            p.drawLine(x, int(mid)-inner, x, int(mid)+inner)
        # Playhead: fixed at the centre of the scrolling area, with a
        # restrained glow under the bright line (pass 9).
        p.setPen(QPen(QColor(255, 255, 255, 42), 7))
        p.drawLine(w//2, 0, w//2, main_h)
        p.setPen(QPen(QColor('#ffffff'), 2))
        p.drawLine(w//2, 0, w//2, main_h)
        # Overview strip: the whole track, thin, with the position
        # marker — the scrolling area's map. Pass 9: the part already
        # played (left of the marker) is dimmed, what is ahead stays
        # full-bright, like rekordbox's overview.
        oy = main_h + 1
        p.setPen(QColor(COLOR_BORDER))
        p.drawLine(0, main_h, w, main_h)
        omid = oy + WAVE_OVERVIEW_HEIGHT/2
        played_pen = QPen(QColor(46, 155, 255, 96), 1)
        ahead_pen = QPen(QColor(self.color), 1)
        pos_x = int(self.position/max(1, total)*w)
        step = max(1, count//max(1, w))
        for i in range(0, count, step):
            x = int((i+0.5)/count*w)
            y = max(1, int(peaks[i]*(WAVE_OVERVIEW_HEIGHT/2-2)))
            p.setPen(played_pen if x < pos_x else ahead_pen)
            p.drawLine(x, int(omid)-y, x, int(omid)+y)
        p.setPen(QPen(QColor('#ffffff'), 2))
        p.drawLine(pos_x, oy, pos_x, oy + WAVE_OVERVIEW_HEIGHT)
    def mousePressEvent(self, event):
        if self.track:
            pos = event.position()
            self.seek.emit(seek_fraction(pos.x(), pos.y(), self.width(),
                                         self.height(), WAVE_OVERVIEW_HEIGHT,
                                         self.position, len(self.track.audio)))

class JogDial(QWidget):
    """Display-only circular BPM dial echoing the rekordbox jog ring:
    grey ring, thin blue inner line, red position marker, huge BPM in
    the centre with the pitch % and range under it. It shows state
    only — MixCraft has no jog control, so it takes no input.

    Pass 5 (user direction, 2026-11-26): both decks run at the set
    BPM, so there is ONE dial, in the centre mixer column, showing
    the SET BPM. With a caption ('SET BPM') the rotating position
    marker and the pitch % / range lines are replaced by the
    caption — a set tempo has no platter position or pitch range."""
    def __init__(self, caption=None, size=118):
        super().__init__()
        self.bpm = None
        self.fraction = 0.0
        self.caption = caption
        self.setFixedSize(size, size)
    def set_bpm(self, bpm):
        self.bpm = bpm
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
        p.setPen(QPen(QColor(COLOR_WAVE_BLUE), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(inner)
        if self.caption is None:
            cx, cy = rect.center().x(), rect.center().y()
            radius = rect.width()/2 - 3
            angle = -math.pi/2 + self.fraction*2*math.pi
            mx, my = cx + radius*math.cos(angle), cy + radius*math.sin(angle)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(COLOR_WAVEFORM_LOW))
            p.drawEllipse(int(mx)-3, int(my)-3, 6, 6)
        bpm_text = f'{self.bpm:.2f}' if self.bpm else '--.--'
        p.setPen(QColor(COLOR_TEXT))
        mono = QFont()
        mono.setFamilies(['Consolas', 'DejaVu Sans Mono', 'Courier New', 'monospace'])
        mono.setPointSize(19 if self.width() <= 124 else 24)
        mono.setBold(True)
        p.setFont(mono)
        p.drawText(inner.adjusted(0, -16, 0, 0), Qt.AlignmentFlag.AlignCenter, bpm_text)
        mono.setPointSize(9)
        mono.setBold(False)
        p.setFont(mono)
        if self.caption is not None:
            p.setPen(QColor(COLOR_TEXT_SUB))
            p.drawText(inner.adjusted(0, 22, 0, 0), Qt.AlignmentFlag.AlignCenter, self.caption)
            return
        p.setPen(QColor(COLOR_TEXT_SUB))
        p.drawText(inner.adjusted(0, 22, 0, 0), Qt.AlignmentFlag.AlignCenter, '0.0%')
        p.setPen(QColor(COLOR_METER))
        p.drawText(inner.adjusted(0, 40, 0, 0), Qt.AlignmentFlag.AlignCenter, '±6')

class MeterLamp(QWidget):
    """Two round level lamps (pass 9): the user's layout sketch draws
    two small circles beside the big SET BPM dial, so the bar meter
    becomes a pair of lamps. Same contract Window.tick() used on the
    old QProgressBar — setRange / setValue(int) / setToolTip — driven
    by the engine's single peak value: lamp 1 lights green with any
    real signal, lamp 2 runs amber then red as the level nears the
    limiter. A two-step glanceable readout, not a precision meter."""
    def __init__(self):
        super().__init__()
        self._value = 0
        self.setFixedSize(72, 32)
    def setRange(self, minimum, maximum):
        pass  # the lamps are threshold-based; the range is not used
    def setValue(self, value):
        value = int(value)
        if value != self._value:
            self._value = value
            self.update()
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        lamps = [(18, QColor(COLOR_METER) if self._value > 4 else None),
                 (54, QColor('#ff3838') if self._value > 92 else
                      QColor('#ffc233') if self._value > 70 else None)]
        for cx, lit in lamps:
            cy = self.height() / 2
            if lit is not None:
                glow = QColor(lit)
                glow.setAlpha(46)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(glow)
                p.drawEllipse(QRectF(cx - 15, cy - 15, 30, 30))
                face = QRadialGradient(cx - 4, cy - 4, 13)
                face.setColorAt(0.0, lit.lighter(160))
                face.setColorAt(1.0, lit)
                p.setBrush(face)
            else:
                p.setBrush(QColor('#101216'))
            p.setPen(QPen(QColor(COLOR_BORDER_HI), 1.2))
            p.drawEllipse(QRectF(cx - 11, cy - 11, 22, 22))

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

    def __init__(self, label, color, bipolar=True):
        super().__init__()
        self.label = label
        self.color = QColor(color)
        self.bipolar = bipolar
        self._min, self._max, self._value = 0, 100, 0
        self._drag_start = None
        self.setFixedSize(64, 52)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

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
        cy = 5 + self.DIAMETER / 2
        radius = self.DIAMETER / 2
        arc_r = radius + 5
        def arc(f_from, f_to, pen):
            p.setPen(pen)
            steps = max(2, int(abs(f_to - f_from) * 90))
            pts = [self._point(cx, cy, arc_r, self._angle(f_from + (f_to - f_from) * i / steps))
                   for i in range(steps + 1)]
            for a, b in zip(pts, pts[1:]):
                p.drawLine(int(a[0]), int(a[1]), int(b[0]), int(b[1]))
        # Full-sweep track, then the value arc in the deck colour.
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
        # like a machined metal cap, and the indicator line now wears
        # the deck colour instead of white, so each column reads as
        # its deck's at a glance.
        cap = QRadialGradient(cx - radius * 0.4, cy - radius * 0.4, radius * 1.35)
        cap.setColorAt(0.0, QColor('#2b2c34'))
        cap.setColorAt(0.55, QColor('#17181c'))
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
        mono = QFont()
        mono.setFamilies(['Consolas', 'DejaVu Sans Mono', 'Courier New', 'monospace'])
        mono.setPointSize(8)
        mono.setBold(True)
        p.setFont(mono)
        p.setPen(QColor(COLOR_TEXT_MUTE))
        p.drawText(QRectF(0, self.height() - 12, self.width(), 12), Qt.AlignmentFlag.AlignCenter, self.label)

class Deck(QFrame):
    def __init__(self, window, index):
        super().__init__()
        self.window, self.index = window, index
        self.setAcceptDrops(True)
        self.setObjectName('deck')
        color = COLOR_DECK_A if index == 0 else COLOR_DECK_B
        # Channel fader styling (vertical, mixer-strip style): for a
        # vertical slider Qt paints the region above the handle (toward
        # maximum) as the sub-page, so the deck colour goes on the
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
                     f'QSlider::add-page:vertical {{background:{color};border-radius:3px}}'
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
        grid.setSpacing(6)
        grid.setContentsMargins(0, 0, 0, 0)
        # Transport: three buttons stacked at the strip's left end,
        # before the title. Same buttons, same wiring as the old row;
        # Deck B's stack mirrors Deck A's top-to-bottom.
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
        for button in ([self.load, self.play, self.cue] if index == 0
                        else [self.cue, self.play, self.load]):
            transport.addWidget(button)
        transport.addStretch()
        grid.addWidget(transport_box, 0, 0)
        # Info block: deck chip + big title, key sub-line, and the
        # BPM / clock readouts in a row under them.
        info_box = QWidget()
        info_box.setFixedWidth(292)
        info = QVBoxLayout(info_box)
        info.setContentsMargins(0, 0, 0, 0)
        info.setSpacing(3)
        title_row = QHBoxLayout()
        chip = QLabel('DECK '+('A' if index == 0 else 'B'))
        chip.setStyleSheet(f'color:{color};background:{COLOR_PANEL_HI};font-size:12px;font-weight:800;padding:4px 8px;border:1px solid {COLOR_BORDER};border-left:6px solid {color};border-radius:2px')
        title_row.addWidget(chip)
        self.name = QLabel('No track loaded / 未読込')
        self.name.setStyleSheet(f'color:{COLOR_TEXT};font-size:17px;font-weight:800;letter-spacing:0.3px')
        self.name.setWordWrap(True)
        title_row.addWidget(self.name, 1)
        info.addLayout(title_row)
        self.sub = QLabel('—')
        self.sub.setStyleSheet(f'color:{COLOR_TEXT_SUB};font-size:11px')
        info.addWidget(self.sub)
        readouts = QHBoxLayout()
        readouts.setSpacing(4)
        self.bpm = QLabel('— BPM')
        self.bpm.setStyleSheet(lcd_qss(19))
        readouts.addWidget(self.bpm)
        self.clock = QLabel('-00:00.0  00:00.0')
        self.clock.setStyleSheet(lcd_qss(12, fg=COLOR_TEXT_SUB))
        readouts.addWidget(self.clock)
        readouts.addStretch()
        info.addLayout(readouts)
        info.addStretch()
        grid.addWidget(info_box, 0, 1)
        self.wave = Waveform(COLOR_WAVE_BLUE)
        self.wave.seek.connect(lambda x: window.engine.command('seek', index, int(x*len(self.wave.track.audio))))
        grid.addWidget(self.wave, 0, 2)
        grid.setColumnStretch(2, 1)
        # Channel strip parts — built here, PLACED by Window in the
        # mixer block (pass 9 sketch layout): the fader in its own
        # box, the five knobs (GAIN / EQ HIGH / EQ MID / EQ LOW /
        # FILTER, centre = neutral) in their own column box. Only the
        # widgets' homes changed — the value domain (0-100), the
        # defaults (volume 80, parameters 50) and every engine command
        # below are exactly what they sent from inside the deck.
        self.fader_box = QWidget()
        fader_box = QVBoxLayout(self.fader_box)
        fader_box.setContentsMargins(0, 0, 0, 0)
        fader_box.setSpacing(4)
        volume_label = QLabel('CHANNEL\nVOLUME')
        volume_label.setStyleSheet(SECTION_LABEL_QSS)
        volume_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        fader_box.addWidget(volume_label)
        self.volume = QSlider(Qt.Orientation.Vertical)
        self.volume.setStyleSheet(fader_qss)
        self.volume.setRange(0, 100)
        self.volume.setValue(80)
        self.volume.setFixedWidth(84)
        self.volume.setFixedHeight(240)
        self.volume.valueChanged.connect(lambda v: window.engine.command('volume', index, v/100))
        fader_box.addWidget(self.volume)
        self.knob_box = QWidget()
        knob_column = QVBoxLayout(self.knob_box)
        knob_column.setContentsMargins(0, 0, 0, 0)
        knob_column.setSpacing(2)
        self.controls = {}
        for label, action, default in [('GAIN', 'gain', 50), ('EQ HIGH', 'eq_high', 50),
                                       ('EQ MID', 'eq_mid', 50), ('EQ LOW', 'eq_low', 50),
                                       ('FILTER', 'filter', 50)]:
            knob = Knob(label, color, bipolar=(action != 'gain'))
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
        quant = QPushButton('Quantize'); quant.setCheckable(True)
        self.quant = quant
        quant.toggled.connect(lambda on, i=index: window.engine.command('quantize', i, on))
        sync = QPushButton('SYNC'); sync.setCheckable(True)
        self.sync = sync
        sync.setToolTip('Tempo match only / テンポのみ一致。音程も変わります。ビート位相同期なし。')
        sync.toggled.connect(lambda on, i=index: window.engine.command('sync', i, on))
        pads_row = QHBoxLayout()
        pads_row.setSpacing(5)
        for button in [cue_set, cue_jump, hot1, hot1s, quant, sync]:
            button.setProperty('pad', True)
            pads_row.addWidget(button)
        grid.addLayout(pads_row, 1, 2)
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
        self.setWindowTitle('MixAble DJ Studio • Mw-fActory • Phase 2 Trial')
        self.resize(1280,900)
        self.engine = Engine()
        self.stream = None
        self.audio_status_count = 0
        self.callback_count = 0
        self.callback_seconds = 0.0
        self.callback_max_seconds = 0.0
        self.hostapi_name = ''
        self.pool = ThreadPoolExecutor(max_workers=2)
        self.result = Result()
        self.result.ready.connect(self.loaded)
        self.loading = set()
        self.setlist_data = None   # loaded set list (normalized dict)
        self.clip_tracks = []      # setlist_to_clip_tracks() result, for the Live hand-off
        self.deck_a_bpm = None     # Deck A track BPM — fallback for the centre SET BPM dial
        self.mix_timer = new_mix_timer()   # pass 8: performance elapsed timer
        data = Path(os.getenv('LOCALAPPDATA', str(Path.home()/'.local/share'))) / 'MixCraftDJStudio'
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
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(5)
        # Slim dark header bar (rekordbox toolbar style) — product name
        # and version stay, the big light title block is gone.
        header_bar = QFrame()
        header_bar.setObjectName('headerbar')
        header = QHBoxLayout(header_bar)
        header.setContentsMargins(10, 3, 10, 3)
        title = QLabel(f'MixAble  <span style="color:{COLOR_DECK_A}">DJ STUDIO</span>')
        title.setStyleSheet('font-size:16px;font-weight:800;letter-spacing:0.5px')
        header.addWidget(title)
        mode_label = QLabel('DJ MODE  •  2 DECK PERFORMANCE  •  PHASE 2 / 試遊版')
        mode_label.setStyleSheet(f'color:{COLOR_TEXT_MUTE};font-size:11px;font-weight:600')
        header.addWidget(mode_label)
        header.addStretch()
        brand = QLabel('MW-FACTORY   /   OFFLINE   /   v0.2.0-trial')
        brand.setStyleSheet(f'color:{COLOR_TEXT_MUTE};font-family:{MONO_STACK};font-size:11px')
        header.addWidget(brand)
        layout.addWidget(header_bar)
        # TOP BAND (pass 9 — the user's hand-drawn sketch, 2026-11-27):
        # the two deck strips STACKED wide, Deck A over Deck B, and at
        # the right end the big SET BPM dial with the two level lamps
        # beside it. The mixer block and the set list form the bottom
        # zone further down (after the utility bar).
        self.decks = [Deck(self,i) for i in range(2)]
        top_band = QHBoxLayout()
        strips = QVBoxLayout()
        strips.setSpacing(4)
        strips.addWidget(self.decks[0])
        strips.addWidget(self.decks[1])
        top_band.addLayout(strips, 1)
        bpm_frame = QFrame()
        bpm_frame.setObjectName('bpmframe')
        bpm_frame.setMinimumWidth(200)
        bpm_lay = QVBoxLayout(bpm_frame)
        # One SET BPM dial for the whole set (pass 5, user direction
        # 2026-11-26): both decks run at the fixed set BPM. Display
        # only — refresh_set_bpm_dial() feeds it from the loaded set
        # list, falling back to Deck A's track BPM.
        set_bpm_label = QLabel('SET BPM / セットBPM')
        set_bpm_label.setStyleSheet(SECTION_LABEL_QSS)
        set_bpm_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bpm_lay.addWidget(set_bpm_label)
        self.set_dial = JogDial(caption='SET BPM', size=148)
        bpm_lay.addWidget(self.set_dial, 0, Qt.AlignmentFlag.AlignCenter)
        # The sketch's two small circles: the level meter as a pair
        # of lamps (MeterLamp keeps the QProgressBar contract tick()
        # already used — setRange / setValue / setToolTip).
        level_label = QLabel('LEVEL')
        level_label.setStyleSheet(SECTION_LABEL_QSS)
        level_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bpm_lay.addWidget(level_label)
        self.meter = MeterLamp()
        self.meter.setRange(0,100)
        bpm_lay.addWidget(self.meter, 0, Qt.AlignmentFlag.AlignCenter)
        bpm_lay.addStretch()
        top_band.addWidget(bpm_frame)
        layout.addLayout(top_band)
        # Set list panel: independent of the decks. Loads the JSON exported
        # by the phone prep page (setlist.load_setlist) and shows the set
        # summary + track order with the phone design's status colours.
        setlist_panel = QFrame()
        setlist_panel.setObjectName('setlist')
        setlist_layout = QVBoxLayout(setlist_panel)
        # Pass 9: the browser header and the MIX TIMER share one
        # bar — the performance clock belongs with the set it times,
        # and merging the two rows hands their height to the track
        # rows below. Same widgets, same wiring as pass 8.
        setlist_head = QHBoxLayout()
        setlist_head.setSpacing(8)
        setlist_label = QLabel('BROWSER / SET LIST')
        setlist_label.setStyleSheet(SECTION_LABEL_QSS)
        setlist_head.addWidget(setlist_label)
        # Mix timer (pass 8, user direction 2026-11-27: 「今何分ミック
        # スしたとかもわかるといいね」): how long the performance has
        # been running, at a glance — MIX mm:ss elapsed, the time left
        # to the set's target length beside it (overage as +mm:ss past
        # the target), and small START/STOP + RESET controls. The
        # timer auto-starts on the first deck play; the math lives in
        # engine.py (pure, unit-tested) and tick() drives it with
        # time.monotonic().
        # (Pass 9: the MIX TIMER caption label left the head row —
        # the readout's own 'MIX' prefix says it, and the row shares
        # the bottom zone's width with the mixer block now.)
        self.mix_elapsed = QLabel('MIX 00:00')
        self.mix_elapsed.setStyleSheet(lcd_qss(20))
        setlist_head.addWidget(self.mix_elapsed)
        self.mix_remaining = QLabel('残り 60:00')
        self.mix_remaining.setStyleSheet(lcd_qss(14, fg=COLOR_TEXT_SUB))
        setlist_head.addWidget(self.mix_remaining)
        setlist_head.addStretch()
        self.mix_toggle = QPushButton('START / 開始')
        self.mix_toggle.clicked.connect(self.toggle_mix_timer)
        setlist_head.addWidget(self.mix_toggle)
        mix_reset = QPushButton('RESET / リセット')
        mix_reset.clicked.connect(self.reset_mix_timer)
        setlist_head.addWidget(mix_reset)
        self.setlist_open = QPushButton('開く / Open setlist JSON')
        self.setlist_open.setToolTip('セットリストを開く / Open setlist JSON')
        self.setlist_open.clicked.connect(self.open_setlist)
        setlist_head.addWidget(self.setlist_open)
        setlist_layout.addLayout(setlist_head)
        setlist_layout.setSpacing(5)
        self.setlist_summary = QLabel('未読込 — スマホの下ごしらえページで書き出した JSON を開いてください')
        self.setlist_summary.setStyleSheet(
            f'background:{COLOR_PANEL_HI};color:{COLOR_TEXT};font-family:{MONO_STACK};'
            f'font-size:12px;border:1px solid {COLOR_BORDER};border-radius:2px;padding:6px 10px')
        self.setlist_summary.setWordWrap(True)
        setlist_layout.addWidget(self.setlist_summary)
        # Browser-style column header for the track rows below.
        columns = QHBoxLayout()
        columns.setContentsMargins(6, 2, 6, 2)
        # Offset matching the row layout: deck bar 5 + chip 22 +
        # status bar 6 + number 34 + the four 6px gaps between them.
        columns.addSpacing(5 + 6 + 22 + 6 + 6 + 6 + 34 + 6)
        head_title = QLabel('TITLE / ARTIST')
        head_title.setStyleSheet(SECTION_LABEL_QSS)
        columns.addWidget(head_title, 1)
        for text, width, align in [('BPM', 64, Qt.AlignmentFlag.AlignRight),
                                   ('KEY', 56, Qt.AlignmentFlag.AlignCenter),
                                   ('TIME', 52, Qt.AlignmentFlag.AlignRight),
                                   ('STATUS', 84, Qt.AlignmentFlag.AlignCenter)]:
            head = QLabel(text)
            head.setStyleSheet(SECTION_LABEL_QSS)
            head.setFixedWidth(width)
            head.setAlignment(align)
            columns.addWidget(head)
        columns.addSpacing(70)
        setlist_layout.addLayout(columns)
        self.setlist_rows = QVBoxLayout()
        self.setlist_rows.setSpacing(2)
        setlist_layout.addLayout(self.setlist_rows)
        # Added to the window layout further down: the set list is the
        # browser panel, so it sits at the bottom (rekordbox layout).
        # Pass 9: transition audition and audio output share ONE
        # slim utility bar between the player and the browser (they
        # are both set-up tools, not performance controls) — one row
        # instead of a caption line plus two rows. Same widgets,
        # same wiring; the audition's fine print rides as a tooltip.
        utility_bar = QFrame()
        utility_bar.setObjectName('utility')
        utility_bar.setToolTip('Aの末尾 → Bの先頭（テンポ同期なし）')
        utility = QHBoxLayout(utility_bar)
        utility.setContentsMargins(8, 3, 8, 3)
        utility.setSpacing(6)
        transition_label = QLabel('TRANSITION / 試聴')
        transition_label.setStyleSheet(SECTION_LABEL_QSS)
        utility.addWidget(transition_label)
        self.duration = QComboBox()
        for seconds in [8,16,32,64]: self.duration.addItem(f'{seconds} seconds / 秒',seconds)
        utility.addWidget(self.duration)
        self.preview_button = QPushButton('▶ Preview transition / ミックス試聴')
        self.preview_button.clicked.connect(self.preview)
        utility.addWidget(self.preview_button)
        stop = QPushButton('Stop preview / 試聴終了')
        stop.clicked.connect(lambda:self.engine.command('stop_preview'))
        utility.addWidget(stop)
        export = QPushButton('Save events JSON / 操作保存')
        export.clicked.connect(self.export)
        utility.addWidget(export)
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.VLine)
        divider.setStyleSheet(f'color:{COLOR_BORDER_HI}')
        utility.addWidget(divider)
        output_label = QLabel('OUTPUT / 出力')
        output_label.setStyleSheet(SECTION_LABEL_QSS)
        utility.addWidget(output_label)
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
        apply = QPushButton('Apply / 適用')
        apply.clicked.connect(self.start_audio)
        utility.addWidget(apply)
        layout.addWidget(utility_bar)
        # BOTTOM ZONE (pass 9 sketch): left, one compact MIXER BLOCK
        # gathering both decks' channel strips — from outside in:
        # Deck A fader | Deck A knobs | Deck B knobs | Deck B fader
        # (faders on the OUTER sides, knob columns in the middle, per
        # the user's standing rule), the MASTER knob at the block's
        # top centre, and the CROSSFADER along its bottom. Right, the
        # SET LIST gets the large remaining area.
        mixer = QFrame()
        mixer.setObjectName('mixer')
        mix = QVBoxLayout(mixer)
        mix.setSpacing(4)
        mix_top = QHBoxLayout()
        mixer_label = QLabel('MIXER')
        mixer_label.setStyleSheet(SECTION_LABEL_QSS)
        mix_top.addWidget(mixer_label)
        mix_top.addStretch()
        # MASTER volume as a knob (pass 6): same Knob widget and the
        # same wiring the horizontal slider had — range 0-100, default
        # 80, 'master' command with v/100. A neutral grey arc and
        # indicator: the master belongs to neither deck.
        self.master = Knob('MASTER', COLOR_TEXT_SUB, bipolar=False)
        self.master.setRange(0, 100)
        self.master.setValue(80)
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
        deck_a_label.setStyleSheet(f'color:{COLOR_DECK_A};font-size:15px;font-weight:800')
        cross_head.addWidget(deck_a_label)
        cross_head.addStretch()
        cross_label = QLabel('CROSSFADER')
        cross_label.setStyleSheet(SECTION_LABEL_QSS)
        cross_head.addWidget(cross_label)
        cross_head.addStretch()
        deck_b_label = QLabel('B')
        deck_b_label.setStyleSheet(f'color:{COLOR_DECK_B};font-size:15px;font-weight:800')
        cross_head.addWidget(deck_b_label)
        mix.addLayout(cross_head)
        self.cross = QSlider(Qt.Orientation.Horizontal)
        self.cross.setRange(0,1000)
        self.cross.setValue(500)
        # The crossfader's machined treatment: the track is tinted
        # toward deck A blue on the left and deck B red on the right
        # (matching the A/B labels above it), and the cap carries the
        # same ridged gradient as the channel fader caps.
        self.cross.setStyleSheet(
            f'QSlider::groove:horizontal {{height:10px;background:qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #10354f, stop:0.5 #060607, stop:1 #4f1d10);border:1px solid {COLOR_BORDER};border-radius:3px}}'
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
        notice = QLabel('BPM・キーは推定値。SYNCはテンポのみ一致し音程も変わります。MIDI入出力・ALS未対応。元ファイルは変更しません。')
        notice.setStyleSheet(f'color:{COLOR_TEXT_MUTE};font-size:11px')
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(40)
        if audio: self.start_audio()
    def start_audio(self):
        if self.stream:
            self.stream.stop(); self.stream.close(); self.stream = None
        device = self.devices.currentData()
        extra = None
        hostapi_name = ''
        try:  # Phase 2: prefer WASAPI (low latency) on Windows when available
            device, hostapi_name = resolve_output(device, sd)
            if 'WASAPI' in hostapi_name:
                extra = sd.WasapiSettings(exclusive=False)
        except Exception:
            extra = None
            device = None if device == 'auto' else device
        try:
            kwargs = dict(samplerate=RATE, channels=2, blocksize=512, latency='low',
                dtype='float32', device=device, callback=self.callback)
            if extra is not None: kwargs['extra_settings'] = extra
            self.stream = sd.OutputStream(**kwargs)
            self.stream.start()
            self.hostapi_name = hostapi_name
            latency_ms = (self.stream.latency or 0) * 1000
            self.status.setText(f'Audio active • 48 kHz / 512 frames • {hostapi_name or "default host"} • latency {latency_ms:.0f} ms (報告値) • 操作を自動保存中')
        except Exception as error:
            self.status.setText(f'Audio unavailable / 出力エラー: {error}')
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
        self.decks[index].name.setText('Loading / 解析中… '+Path(path).name)
        self.decks[index].load.setEnabled(False)
        def work():
            try: self.result.ready.emit(index,load_track(path),'')
            except Exception as e: self.result.ready.emit(index,None,str(e))
        self.pool.submit(work)
    def loaded(self,index,track,error):
        self.loading.discard(index)
        deck = self.decks[index]
        deck.load.setEnabled(True)
        if error:
            deck.name.setText('Load failed / 読込失敗')
            QMessageBox.warning(self,'Audio load',error)
            return
        self.engine.command('stop_preview')
        self.engine.command('load',index,track)
        deck.wave.track = track
        deck.name.setText(Path(track.path).name)
        deck.sub.setText(f'Key {track.key} ({track.camelot})' if track.key else '—')
        deck.bpm.setText(f'{track.bpm or "—"} BPM ≈')
        deck.bpm.setToolTip(f'Estimate confidence: {track.confidence:.0%}. Half/double tempo errors possible.')
        if index == 0:
            self.deck_a_bpm = track.bpm
            self.refresh_set_bpm_dial()
    def preview(self):
        if not all(d['track'] for d in self.engine.decks):
            self.status.setText('Load both decks first / 両デッキに音声を読み込んでください')
            return
        self.engine.command('preview',value=self.duration.currentData())
    def export(self):
        path,_ = QFileDialog.getSaveFileName(self,'Save events','mixcraft-events.json','JSON (*.json)')
        if path:
            self.journal.drain(self.engine)
            try: self.journal.export(path)
            except OSError as e: QMessageBox.warning(self,'Save failed',str(e))
    def open_setlist(self):
        path,_ = QFileDialog.getOpenFileName(self,'Setlist JSON / セットリスト','','Setlist JSON (*.json)')
        if not path: return
        try:
            data = load_setlist(path)
        except SetlistFormatError as error:
            self.status.setText(f'セットリスト読込エラー / Setlist error: {error}')
            return
        self.setlist_data = data
        self.clip_tracks = setlist_to_clip_tracks(data)
        self.render_setlist()
        self.refresh_set_bpm_dial()
        summary = display_summary(data)
        self.status.setText(f'セットリスト読込 / Setlist loaded: {Path(path).name} • {summary["track_count"]} 曲 • 合計 {summary["total_text"]} • クリップ変換 {len(self.clip_tracks)} 件を保持中')
    def mix_target_seconds(self):
        """Set target length for the mix timer: the loaded set list's
        target minutes (setlist.py carries target_minutes), else the
        60-minute default."""
        minutes = None
        if self.setlist_data is not None:
            minutes = self.setlist_data.get('target_minutes')
        if minutes is None:
            minutes = DEFAULT_TARGET_MINUTES
        return float(minutes) * 60
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
        elapsed = self.mix_timer['elapsed']
        self.mix_elapsed.setText(f'MIX {format_mix_clock(elapsed)}')
        remaining = format_mix_remaining(elapsed, self.mix_target_seconds())
        caption = '超過' if remaining.startswith('+') else '残り'
        self.mix_remaining.setText(f'{caption} {remaining}')
        self.mix_toggle.setText(
            'STOP / 停止' if self.mix_timer['running'] else 'START / 開始')
    def refresh_set_bpm_dial(self):
        """Feed the centre SET BPM dial: the loaded set list's set
        BPM wins (the set runs at that fixed tempo); without a set
        list, fall back to Deck A's track BPM; with neither, the
        dial shows its neutral placeholder."""
        bpm = None
        if self.setlist_data is not None:
            bpm = display_summary(self.setlist_data)['set_bpm']
        if bpm is None:
            bpm = self.deck_a_bpm
        self.set_dial.set_bpm(bpm)
    def render_setlist(self):
        while self.setlist_rows.count():
            item = self.setlist_rows.takeAt(0)
            widget = item.widget()
            if widget: widget.deleteLater()
        if self.setlist_data is None: return
        summary = display_summary(self.setlist_data)
        bpm_text = f'{summary["set_bpm"]:g}' if summary['set_bpm'] is not None else '—'
        self.setlist_summary.setText(
            f'SET BPM {bpm_text}  •  {summary["track_count"]} TRACKS  •  TOTAL {summary["total_text"]}  •  {summary["state_text"]}')
        colors = {'played': COLOR_STATE_PLAYED, 'playing': COLOR_STATE_PLAYING,
                  'next': COLOR_STATE_NEXT, 'queued': COLOR_STATE_QUEUED}
        for row_index, row in enumerate(display_rows(self.setlist_data)):
            color = colors[row['status']]
            # Deck indicator (pass 6, user direction 2026-11-26): the
            # decks are colour-coded (A blue / B red-orange) and the
            # track list now shows the same coding, so the deck a track
            # belongs to reads at a glance. Assignment follows the
            # established alternation (live_plan.py): odd positions
            # (1,3,5...) -> Deck A, even (2,4,6...) -> Deck B, from the
            # row's position in the list. This is a COMPLEMENT to the
            # status colours, not a replacement: the status bar, number
            # and badge below keep the fixed played / playing / next /
            # queued colours exactly as they are — the deck channel is
            # separate: a deck-coloured bar at the row's left edge plus
            # an 'A' / 'B' letter chip.
            deck_color = COLOR_DECK_A if row['number'] % 2 == 1 else COLOR_DECK_B
            deck_letter = 'A' if row['number'] % 2 == 1 else 'B'
            line = QHBoxLayout()
            line.setContentsMargins(6, 3, 6, 3)
            deck_bar = QFrame()
            deck_bar.setFixedWidth(5)
            deck_bar.setStyleSheet(f'background:{deck_color};border:none')
            line.addWidget(deck_bar)
            chip = QLabel(deck_letter)
            chip.setFixedWidth(22)
            chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
            chip.setStyleSheet(f'color:{deck_color};background:{COLOR_PANEL_HI};border:1px solid {deck_color};border-radius:2px;font-size:11px;font-weight:800')
            line.addWidget(chip)
            bar = QFrame()
            bar.setFixedWidth(6)
            bar.setStyleSheet(f'background:{color};border:none')
            line.addWidget(bar)
            number = QLabel(f'{row["number"]:02d}')
            number.setStyleSheet(f'color:{color};font-family:{MONO_STACK};font-size:14px;font-weight:700')
            number.setFixedWidth(34)
            line.addWidget(number)
            title_text = row['title'] or '(無題)'
            if row['artist']: title_text += f' — {row["artist"]}'
            title = QLabel(title_text)
            title.setStyleSheet(f'color:{COLOR_STATE_PLAYED if row["status"]=="played" else COLOR_TEXT};font-size:13px;font-weight:600')
            title.setWordWrap(True)
            line.addWidget(title, 1)
            meta_qss = f'color:{COLOR_TEXT_SUB};font-family:{MONO_STACK};font-size:12px'
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
            badge = QLabel(row['status_label'])
            badge.setStyleSheet(f'color:{color};font-family:{MONO_STACK};font-size:11px;font-weight:700')
            badge.setFixedWidth(84)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            line.addWidget(badge)
            link_slot = QWidget()
            link_slot.setFixedWidth(70)
            if row['link_url']:
                link_layout = QHBoxLayout(link_slot)
                link_layout.setContentsMargins(0, 0, 0, 0)
                link_button = QPushButton('試聴 ↗')
                link_button.setToolTip(row['link_url'])
                link_button.clicked.connect(lambda _checked=False, url=row['link_url']: QDesktopServices.openUrl(QUrl(url)))
                link_layout.addWidget(link_button)
            line.addWidget(link_slot)
            # Striped browser rows, like his collection table.
            # Pass 9: the NOW PLAYING row also gets a faint green
            # wash of its own (object name below) — a restrained
            # glow on top of the fixed status colours, which the
            # bar / number / badge keep unchanged.
            container = QFrame()
            if row['status'] == 'playing':
                container.setObjectName('setrowplaying')
            else:
                container.setObjectName(f'setrow{row_index % 2}')
            container.setLayout(line)
            self.setlist_rows.addWidget(container)
    def tick(self):
        try: self.journal.drain(self.engine)
        except OSError as e: self.status.setText(f'Autosave error: {e}')
        for widget,d in zip(self.decks,self.engine.decks):
            for button, key in [(widget.quant,'quantize'),(widget.sync,'sync')]:
                button.blockSignals(True); button.setChecked(d[key]); button.blockSignals(False)
            # Pass 7: repaint the (scrolling) waveform only when the
            # position actually moved or the loaded track changed —
            # no busy repaint while a deck sits paused.
            if (widget.wave.position != d['position']
                    or widget.wave.track is not d['track']):
                widget.wave.position = d['position']
                widget.wave.update()
            widget.play.setText('Ⅱ Pause / 一時停止' if d['playing'] else '▶ Play / 再生')
            length = len(d['track'].audio)/RATE if d['track'] else 0
            def clock(s):
                s = max(0.0, s)
                return f'{int(s)//60:02}:{s%60:04.1f}'
            elapsed = d['position']/RATE
            # rekordbox shows remaining time big, elapsed beside it.
            widget.clock.setText(f'-{clock(length-elapsed)}  {clock(elapsed)}')
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
    def closeEvent(self,event):
        self.timer.stop()
        self.pool.shutdown(wait=True)
        if self.stream: self.stream.stop(); self.stream.close()
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
    f'QFrame#headerbar {{ background:qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #23242a, stop:0.06 #141519, stop:1 #0e0e11); border:1px solid {COLOR_BORDER}; border-radius:5px; }}'
    f'QFrame#deck {{ background:qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #33363f, stop:0.004 #1d1f26, stop:0.5 #131418, stop:1 #0e0f12); border:1px solid #26282f; border-radius:6px; padding:8px; }}'
    f'QFrame#mixer, QFrame#bpmframe {{ background:qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #33363f, stop:0.004 #1e2027, stop:0.5 #14151a, stop:1 #0f1013); border:1px solid #26282f; border-radius:6px; padding:8px 12px; }}'
    f'QFrame#setlist {{ background:qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #33363f, stop:0.01 #1d1f26, stop:0.4 #131418, stop:1 #0f1013); border:1px solid #26282f; border-radius:6px; padding:8px 12px; }}'
    f'QFrame#utility {{ background:qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #2b2d34, stop:0.05 #17181d, stop:1 #101114); border:1px solid #26282f; border-radius:6px; }}'
    f'QFrame#setrow0 {{ background:#101114; border:1px solid #1b1c21; border-radius:4px; }}'
    f'QFrame#setrow1 {{ background:#16171c; border:1px solid #1e2026; border-radius:4px; }}'
    f'QFrame#setrowplaying {{ background:qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #16280f, stop:1 #0d1a0a); border:1px solid #33512a; border-radius:4px; }}'
    f'QPushButton {{ background:qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1e1f25, stop:1 #121317); border:1px solid #2c2e36; border-radius:4px; padding:6px 10px; font-weight:600; color:{COLOR_TEXT}; }}'
    f'QPushButton:hover {{ background:qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #282a31, stop:1 #17181d); border-color:#3d404a; }}'
    f'QPushButton:pressed {{ background:#0a0a0d; border-color:#1d1f24; }}'
    f'QPushButton:disabled {{ background:#121317; border-color:#1d1f24; color:#565b63; }}'
    f'QPushButton:checked {{ background:qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #0e4066, stop:1 #082a47); border-color:{COLOR_DECK_A}; color:{COLOR_WAVE_LIGHT}; font-weight:800; }}'
    # Performance pads: near-black cells, hairline borders, small caps.
    f'QPushButton[pad="true"] {{ background:qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #14151a, stop:1 #0a0a0c); border:1px solid #26282f; border-radius:3px; padding:9px 4px; font-size:11px; font-weight:700; color:#c9ccd1; }}'
    f'QPushButton[pad="true"]:hover {{ background:qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1a1b21, stop:1 #0e0f12); border-color:#3f434e; }}'
    f'QPushButton[pad="true"]:pressed {{ background:#060607; }}'
    f'QPushButton[pad="true"]:checked {{ background:qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #0e4066, stop:1 #082a47); border-color:{COLOR_DECK_A}; color:{COLOR_WAVE_LIGHT}; }}'
    f'QComboBox {{ background:#17181d; border:1px solid #2a2c33; border-radius:3px; padding:6px 9px; color:{COLOR_TEXT}; }}'
    f'QComboBox:hover {{ border-color:#3a3d46; }}'
    f'QComboBox::drop-down {{ border:none; width:18px; }}'
    f'QComboBox QAbstractItemView {{ background:#17181d; color:{COLOR_TEXT}; border:1px solid #2a2c33; selection-background-color:#0a2e4d; }}'
    f'QSlider::groove:horizontal {{ height:8px; background:#060607; border:1px solid {COLOR_BORDER}; border-radius:2px; }}'
    f'QSlider::sub-page:horizontal {{ background:#3a3d46; border-radius:2px; }}'
    f'QSlider::handle:horizontal {{ width:14px; height:24px; margin:-9px 0; background:qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #d8d8d8, stop:0.44 #d8d8d8, stop:0.5 #1c1d1f, stop:0.56 #d8d8d8, stop:1 #d8d8d8); border:2px solid #000000; border-radius:2px; }}'
    f'QProgressBar {{ background:{COLOR_DISPLAY_BG}; border:1px solid {COLOR_BORDER}; border-radius:3px; min-height:15px; color:{COLOR_DISPLAY_FG}; font-family:{MONO_STACK}; }}'
    f'QProgressBar::chunk {{ background:qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1fa34a, stop:0.75 #2ee66b, stop:0.92 #ffc233, stop:1 #ff3838); border-radius:1px; }}'
    f'QLabel#status {{ background:{LCD_GRADIENT}; border:1px solid {COLOR_BORDER}; border-radius:2px; padding:5px 10px; color:{COLOR_DISPLAY_FG}; font-family:{MONO_STACK}; font-size:13px; }}'
    f'QToolTip {{ background:{COLOR_PANEL_HI}; color:{COLOR_TEXT}; border:1px solid {COLOR_BORDER_HI}; padding:4px 6px; }}'
    f'QScrollBar:vertical {{ background:{COLOR_BG}; width:12px; margin:0; }}'
    f'QScrollBar::handle:vertical {{ background:#2c2f36; border-radius:2px; min-height:30px; }}'
    f'QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}'
    f'QScrollBar:horizontal {{ background:{COLOR_BG}; height:12px; margin:0; }}'
    f'QScrollBar::handle:horizontal {{ background:#2c2f36; border-radius:2px; min-width:30px; }}'
    f'QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width:0; }}'
)

if __name__ == '__main__':
    app = QApplication(sys.argv)
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
