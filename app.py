import math
import os
import sys
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import sounddevice as sd
from PySide6.QtCore import Qt, QTimer, Signal, QObject, QUrl, QRectF
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QDesktopServices
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QSlider, QFileDialog, QComboBox, QMessageBox, QFrame, QProgressBar, QScrollArea)
from engine import Engine, Journal, RATE, load_track
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
SECTION_LABEL_QSS = f'color:{COLOR_TEXT_MUTE};font-family:{MONO_STACK};font-size:10px;font-weight:700'
LCD_GRADIENT = ('qlineargradient(x1:0, y1:0, x2:0, y2:1, '
                'stop:0 #0a0a0c, stop:0.45 #050506, stop:1 #070708)')

def lcd_qss(size, bold=True, fg=None):
    """Dark-glass numeric readout style for display-only labels."""
    return (f'background:{LCD_GRADIENT};color:{fg or COLOR_DISPLAY_FG};'
            f'font-family:{MONO_STACK};font-size:{size}px;'
            f'font-weight:{700 if bold else 400};'
            f'border:1px solid {COLOR_BORDER};border-radius:2px;padding:4px 10px')

class Result(QObject):
    ready = Signal(int, object, str)

class Waveform(QWidget):
    seek = Signal(float)
    def __init__(self, color):
        super().__init__()
        self.track = None
        self.position = 0
        self.color = color
        self.setMinimumHeight(104)
    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(COLOR_WAVE_BG))
        p.setPen(QColor(COLOR_WAVE_GRID))
        for x in range(0, self.width(), 40): p.drawLine(x, 0, x, self.height())
        mid = self.height()/2
        p.setPen(QColor(COLOR_BORDER))
        p.drawLine(0, int(mid), self.width(), int(mid))
        if self.track:
            # rekordbox blue scheme: deep blue body, lighter core —
            # the same on both decks, like the real thing.
            body = QPen(QColor(self.color), 1)
            core = QPen(QColor(COLOR_WAVE_LIGHT), 1)
            for i, peak in enumerate(self.track.peaks):
                x = int(i*self.width()/len(self.track.peaks))
                y = int(peak*(mid-8))
                p.setPen(body)
                p.drawLine(x, int(mid)-y, x, int(mid)+y)
                inner = int(y*0.45)
                p.setPen(core)
                p.drawLine(x, int(mid)-inner, x, int(mid)+inner)
            x = int(self.position/max(1,len(self.track.audio))*self.width())
            p.setPen(QPen(QColor('#ffffff'), 2))
            p.drawLine(x, 0, x, self.height())
        else:
            p.setPen(QColor(COLOR_TEXT_MUTE))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, 'DROP AUDIO HERE  /  音声ファイルをドロップ')
    def mousePressEvent(self, event):
        if self.track: self.seek.emit(event.position().x()/self.width())

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
    def __init__(self, caption=None):
        super().__init__()
        self.bpm = None
        self.fraction = 0.0
        self.caption = caption
        self.setFixedSize(118, 118)
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
        p.setPen(QPen(QColor(COLOR_BORDER_HI), 3))
        p.setBrush(QColor('#0b0b0d'))
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
        mono.setPointSize(19)
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
    DIAMETER = 44

    def __init__(self, label, color, bipolar=True):
        super().__init__()
        self.label = label
        self.color = QColor(color)
        self.bipolar = bipolar
        self._min, self._max, self._value = 0, 100, 0
        self._drag_start = None
        self.setFixedSize(72, 66)
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
        cy = 8 + self.DIAMETER / 2
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
        # Knob body and its white indicator line.
        p.setPen(QPen(QColor(COLOR_BORDER_HI), 1.5))
        p.setBrush(QColor('#17181c'))
        p.drawEllipse(QRectF(cx - radius, cy - radius, self.DIAMETER, self.DIAMETER))
        angle = self._angle(fraction)
        x1, y1 = self._point(cx, cy, radius * 0.42, angle)
        x2, y2 = self._point(cx, cy, radius * 0.88, angle)
        p.setPen(QPen(QColor(COLOR_TEXT), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(int(x1), int(y1), int(x2), int(y2))
        # Label beneath the knob.
        mono = QFont()
        mono.setFamilies(['Consolas', 'DejaVu Sans Mono', 'Courier New', 'monospace'])
        mono.setPointSize(8)
        mono.setBold(True)
        p.setFont(mono)
        p.setPen(QColor(COLOR_TEXT_MUTE))
        p.drawText(QRectF(0, 54, self.width(), 12), Qt.AlignmentFlag.AlignCenter, self.label)

class Deck(QFrame):
    def __init__(self, window, index):
        super().__init__()
        self.window, self.index = window, index
        self.setAcceptDrops(True)
        self.setObjectName('deck')
        lay = QVBoxLayout(self)
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
        fader_qss = (f'QSlider::groove:vertical {{width:24px;background:#0c0d0e;border:1px solid {COLOR_BORDER};border-radius:3px}}'
                     f'QSlider::sub-page:vertical {{background:#0c0d0e;border-radius:3px}}'
                     f'QSlider::add-page:vertical {{background:{color};border-radius:3px}}'
                     f'QSlider::handle:vertical {{height:20px;width:84px;margin:0 -30px;background:qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #d8d8d8, stop:0.44 #d8d8d8, stop:0.5 #1c1d1f, stop:0.56 #d8d8d8, stop:1 #d8d8d8);border:2px solid #000000;border-radius:3px}}')
        self.fader_qss = fader_qss
        # Deck info strip, rekordbox-style: small deck chip, big title
        # with a sub line under it, then BPM and time readouts right.
        top = QHBoxLayout()
        label = QLabel('DECK '+('A' if index == 0 else 'B'))
        label.setStyleSheet(f'color:{color};background:{COLOR_PANEL_HI};font-size:12px;font-weight:800;padding:4px 8px;border:1px solid {COLOR_BORDER};border-left:6px solid {color};border-radius:2px')
        top.addWidget(label)
        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        self.name = QLabel('No track loaded / 未読込')
        self.name.setStyleSheet(f'color:{COLOR_TEXT};font-size:16px;font-weight:700')
        self.name.setWordWrap(True)
        title_box.addWidget(self.name)
        self.sub = QLabel('—')
        self.sub.setStyleSheet(f'color:{COLOR_TEXT_SUB};font-size:11px')
        title_box.addWidget(self.sub)
        top.addLayout(title_box, 1)
        # Readouts stacked on the right so the title keeps its width
        # (side-by-side, the filename wrapped to three lines).
        readouts = QVBoxLayout()
        readouts.setSpacing(3)
        self.bpm = QLabel('— BPM')
        self.bpm.setStyleSheet(lcd_qss(20))
        readouts.addWidget(self.bpm)
        self.clock = QLabel('-00:00.0  00:00.0')
        self.clock.setStyleSheet(lcd_qss(13))
        readouts.addWidget(self.clock)
        top.addLayout(readouts)
        lay.addLayout(top)
        self.wave = Waveform(COLOR_WAVE_BLUE)
        self.wave.seek.connect(lambda x: window.engine.command('seek', index, int(x*len(self.wave.track.audio))))
        lay.addWidget(self.wave)
        row = QHBoxLayout()
        self.load = QPushButton('Load / 読込')
        self.load.clicked.connect(self.choose)
        self.play = QPushButton('▶ Play / 再生')
        self.play.clicked.connect(lambda: window.engine.command('play', index, not window.engine.decks[index]['playing']))
        self.cue = QPushButton('■ Reset / 先頭')
        self.cue.clicked.connect(self.reset)
        # Mirror symmetry (user direction, 2026-11-26): Deck B's
        # transport row mirrors Deck A's about the window centre —
        # Reset innermost, Load at the outer edge — like the PLAY/CUE
        # placement on dual-deck gear. Same buttons, same wiring.
        for button in ([self.load, self.play, self.cue] if index == 0
                        else [self.cue, self.play, self.load]):
            row.addWidget(button)
        lay.addLayout(row)
        # Channel strip (user direction, 2026-11-26): the channel
        # volume becomes a VERTICAL fader and GAIN / EQ / FILTER become
        # rotary KNOBS in a column beside the pad grid,
        # like a hardware mixer channel strip. Only the widgets and
        # layout changed — the value domain (0-100), the defaults
        # (volume 80, parameters 50 = neutral) and every engine command
        # below are exactly what the horizontal sliders sent.
        strip = QHBoxLayout()
        strip.setSpacing(8)
        fader_box = QVBoxLayout()
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
        self.volume.setFixedHeight(312)
        self.volume.valueChanged.connect(lambda v: window.engine.command('volume', index, v/100))
        fader_box.addWidget(self.volume)
        # (Added to `strip` at the end, in mirror order — see below.)
        # Phase 2: GAIN / 3-band EQ / FILTER as knobs (centre = neutral)
        knob_column = QVBoxLayout()
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
        knob_column.addStretch()
        # (Added to `strip` at the end, in mirror order — see below.)
        # Phase 2: CUE / HOT CUE / QUANTIZE / SYNC — laid out as a
        # dark performance-pad grid (3x2). Same buttons, same wiring.
        # (Pass 5: the jog-style BPM dial left the decks — both decks
        # run at the set BPM, so the single dial lives in the centre
        # mixer column showing the SET BPM; see Window. The Loop In /
        # Out / Exit pads also left in pass 5, by the user's ruling:
        # they are standalone-era leftovers that are NOT connected to
        # the Live / hardware performance path — the DDJ-RB's own loop
        # buttons are not assigned in the Remote Script either — so
        # they do not belong on screen. The engine's loop commands
        # stay in the engine, unpresented.)
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
        pads = QGridLayout()
        pads.setSpacing(5)
        for i, button in enumerate([cue_set, cue_jump, hot1,
                                    hot1s, quant, sync]):
            button.setProperty('pad', True)
            pads.addWidget(button, i // 3, i % 3)
        # The pad grid sits alongside the fader / knob strip, centred
        # vertically; the strip's height is set by the knob column.
        # With the jog dial gone (pass 5) the grid takes the freed
        # outer space and stretches to fill it.
        perf_box = QVBoxLayout()
        perf_box.addStretch()
        perf_box.addLayout(pads)
        perf_box.addStretch()
        # Mixer-centre symmetry (user direction, 2026-11-26, pass 5 —
        # this CORRECTS pass 4, which mirrored the wrong way): like a
        # DJ mixer standing between two decks, the mixer parts gather
        # at the centre and the pads sit at the outer sides. Pass 6
        # (user direction, same day): the channel fader moves one step
        # OUTWARD, to the outside of its EQ knob column — the side of
        # the knobs away from the centre. Deck A now reads
        # [pads | fader | knobs] and Deck B [knobs | fader | pads], so
        # the two knob columns flank the centre mixer column and each
        # fader stands directly beside its own knobs. Layout only:
        # same widgets, values and engine wiring on both decks; the
        # knob column's vertical order (GAIN top -> FILTER bottom)
        # does not mirror.
        if index == 0:
            strip.addLayout(perf_box, 1)
            strip.addLayout(fader_box)
            strip.addLayout(knob_column)
        else:
            strip.addLayout(knob_column)
            strip.addLayout(fader_box)
            strip.addLayout(perf_box, 1)
        lay.addLayout(strip)
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
        data = Path(os.getenv('LOCALAPPDATA', str(Path.home()/'.local/share'))) / 'MixCraftDJStudio'
        self.journal = Journal(data/'sessions'/f'{time.time_ns()}.jsonl')
        root = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(root)
        self.setCentralWidget(scroll)
        layout = QVBoxLayout(root)
        # Slim dark header bar (rekordbox toolbar style) — product name
        # and version stay, the big light title block is gone.
        header_bar = QFrame()
        header_bar.setObjectName('headerbar')
        header = QHBoxLayout(header_bar)
        header.setContentsMargins(10, 5, 10, 5)
        title = QLabel(f'MixAble  <span style="color:{COLOR_DECK_A}">DJ STUDIO</span>')
        title.setStyleSheet('font-size:15px;font-weight:800')
        header.addWidget(title)
        mode_label = QLabel('DJ MODE  •  2 DECK PERFORMANCE  •  PHASE 2 / 試遊版')
        mode_label.setStyleSheet(f'color:{COLOR_TEXT_MUTE};font-size:11px;font-weight:600')
        header.addWidget(mode_label)
        header.addStretch()
        brand = QLabel('MW-FACTORY   /   OFFLINE   /   v0.2.0-trial')
        brand.setStyleSheet(f'color:{COLOR_TEXT_MUTE};font-family:{MONO_STACK};font-size:11px')
        header.addWidget(brand)
        layout.addWidget(header_bar)
        # Performance area (rekordbox layout): Deck A | centre mixer
        # column | Deck B, roughly 5:2:5 like the rekordbox player panel.
        self.decks = [Deck(self,i) for i in range(2)]
        perf = QHBoxLayout()
        perf.addWidget(self.decks[0], 5)
        mixer = QFrame()
        mixer.setObjectName('mixer')
        mixer.setMinimumWidth(210)
        mix = QVBoxLayout(mixer)
        mixer_label = QLabel('MIXER')
        mixer_label.setStyleSheet(SECTION_LABEL_QSS)
        mixer_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mix.addWidget(mixer_label)
        # Centre column order (pass 6, user direction 2026-11-26):
        # SET BPM dial up top, then the level meter, then MASTER as a
        # rotary knob, and the CROSSFADER at the very bottom — like a
        # real DJ mixer, where the crossfader sits under the channel
        # faders at the bottom of the mixer section. The stretch above
        # the crossfader pins it to the bottom of the column, roughly
        # level with the bottom of the deck channel strips.
        # One SET BPM dial for the whole set (pass 5, user direction
        # 2026-11-26): both decks run at the fixed set BPM, so the
        # jog-style dial moved here from the decks. Display only —
        # refresh_set_bpm_dial() feeds it from the loaded set list,
        # falling back to Deck A's track BPM.
        set_bpm_label = QLabel('SET BPM / セットBPM')
        set_bpm_label.setStyleSheet(SECTION_LABEL_QSS)
        set_bpm_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mix.addWidget(set_bpm_label)
        self.set_dial = JogDial(caption='SET BPM')
        mix.addWidget(self.set_dial, 0, Qt.AlignmentFlag.AlignCenter)
        self.meter = QProgressBar()
        self.meter.setRange(0,100)
        self.meter.setTextVisible(False)
        mix.addWidget(self.meter)
        # MASTER volume as a knob (pass 6): same Knob widget and the
        # same wiring the horizontal slider had — range 0-100, default
        # 80, 'master' command with v/100. The label is the knob's own.
        # A neutral grey arc: the master belongs to neither deck.
        self.master = Knob('MASTER', COLOR_TEXT_SUB, bipolar=False)
        self.master.setRange(0, 100)
        self.master.setValue(80)
        self.master.valueChanged.connect(lambda v: self.engine.command('master', value=v/100))
        mix.addWidget(self.master, 0, Qt.AlignmentFlag.AlignCenter)
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
        self.cross.valueChanged.connect(lambda v:self.engine.command('cross',value=v/1000))
        mix.addWidget(self.cross)
        perf.addWidget(mixer, 2)
        perf.addWidget(self.decks[1], 5)
        layout.addLayout(perf)
        # Set list panel: independent of the decks. Loads the JSON exported
        # by the phone prep page (setlist.load_setlist) and shows the set
        # summary + track order with the phone design's status colours.
        setlist_panel = QFrame()
        setlist_panel.setObjectName('setlist')
        setlist_layout = QVBoxLayout(setlist_panel)
        setlist_head = QHBoxLayout()
        setlist_label = QLabel('BROWSER / SET LIST — セットリスト（スマホ下ごしらえ）')
        setlist_label.setStyleSheet(SECTION_LABEL_QSS)
        setlist_head.addWidget(setlist_label)
        setlist_head.addStretch()
        self.setlist_open = QPushButton('セットリストを開く / Open setlist JSON')
        self.setlist_open.clicked.connect(self.open_setlist)
        setlist_head.addWidget(self.setlist_open)
        setlist_layout.addLayout(setlist_head)
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
        self.setlist_rows.setSpacing(0)
        setlist_layout.addLayout(self.setlist_rows)
        # Added to the window layout further down: the set list is the
        # browser panel, so it sits at the bottom (rekordbox layout).
        transition_label = QLabel('TRANSITION AUDITION / ミックス試聴  —  Aの末尾 → Bの先頭（テンポ同期なし）')
        transition_label.setStyleSheet(SECTION_LABEL_QSS)
        layout.addWidget(transition_label)
        preview = QHBoxLayout()
        self.duration = QComboBox()
        for seconds in [8,16,32,64]: self.duration.addItem(f'{seconds} seconds / 秒',seconds)
        preview.addWidget(self.duration)
        self.preview_button = QPushButton('▶ Preview transition / ミックス試聴')
        self.preview_button.clicked.connect(self.preview)
        preview.addWidget(self.preview_button)
        stop = QPushButton('Stop preview / 試聴終了')
        stop.clicked.connect(lambda:self.engine.command('stop_preview'))
        preview.addWidget(stop)
        export = QPushButton('Save events JSON / 操作保存')
        export.clicked.connect(self.export)
        preview.addWidget(export)
        layout.addLayout(preview)
        settings = QHBoxLayout()
        output_label = QLabel('AUDIO OUTPUT / 出力')
        output_label.setStyleSheet(SECTION_LABEL_QSS)
        settings.addWidget(output_label)
        self.devices = QComboBox()
        self.devices.addItem('Automatic / WASAPI優先', 'auto')
        self.devices.addItem('System default / システム既定', None)
        try:
            hosts = sd.query_hostapis()
            for i,d in enumerate(sd.query_devices()):
                if d['max_output_channels'] >= 2: self.devices.addItem(d['name']+' / '+hosts[d['hostapi']]['name'],i)
        except Exception: pass
        settings.addWidget(self.devices,1)
        apply = QPushButton('Apply / 適用')
        apply.clicked.connect(self.start_audio)
        settings.addWidget(apply)
        layout.addLayout(settings)
        layout.addWidget(setlist_panel, 1)
        self.status = QLabel('Ready / 準備完了')
        self.status.setObjectName('status')
        layout.addWidget(self.status)
        notice = QLabel('BPM・キーは推定値。SYNCはテンポのみ一致し音程も変わります。MIDI入出力・ALS未対応。元ファイルは変更しません。')
        notice.setStyleSheet(f'color:{COLOR_TEXT_MUTE};font-size:12px')
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
            line.setContentsMargins(6, 4, 6, 4)
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
            container = QFrame()
            container.setObjectName(f'setrow{row_index % 2}')
            container.setLayout(line)
            self.setlist_rows.addWidget(container)
    def tick(self):
        try: self.journal.drain(self.engine)
        except OSError as e: self.status.setText(f'Autosave error: {e}')
        for widget,d in zip(self.decks,self.engine.decks):
            for button, key in [(widget.quant,'quantize'),(widget.sync,'sync')]:
                button.blockSignals(True); button.setChecked(d[key]); button.blockSignals(False)
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
    f'QFrame#headerbar {{ background:#101114; border:1px solid {COLOR_BORDER}; border-radius:3px; }}'
    f'QFrame#deck {{ background:{COLOR_PANEL}; border:1px solid {COLOR_BORDER}; border-radius:3px; padding:10px; }}'
    f'QFrame#mixer {{ background:{COLOR_PANEL}; border:1px solid {COLOR_BORDER}; border-radius:3px; padding:10px 12px; }}'
    f'QFrame#setlist {{ background:{COLOR_PANEL}; border:1px solid {COLOR_BORDER}; border-radius:3px; padding:10px 12px; }}'
    f'QFrame#setrow0 {{ background:#101114; border:none; border-bottom:1px solid {COLOR_BORDER}; }}'
    f'QFrame#setrow1 {{ background:#16171c; border:none; border-bottom:1px solid {COLOR_BORDER}; }}'
    f'QPushButton {{ background:#17181d; border:1px solid #2a2c33; border-radius:3px; padding:7px 10px; font-weight:600; color:{COLOR_TEXT}; }}'
    f'QPushButton:hover {{ background:{COLOR_BUTTON_HOVER}; border-color:#3a3d46; }}'
    f'QPushButton:pressed {{ background:#0c0c0f; }}'
    f'QPushButton:disabled {{ background:#121317; border-color:#1d1f24; color:#565b63; }}'
    f'QPushButton:checked {{ background:#0a2e4d; border-color:{COLOR_DECK_A}; color:{COLOR_WAVE_LIGHT}; font-weight:800; }}'
    # Performance pads: near-black cells, hairline borders, small caps.
    f'QPushButton[pad="true"] {{ background:#0e0f12; border:1px solid #26282f; border-radius:2px; padding:9px 4px; font-size:11px; font-weight:700; color:#c9ccd1; }}'
    f'QPushButton[pad="true"]:hover {{ background:#14151a; border-color:#3f434e; }}'
    f'QPushButton[pad="true"]:pressed {{ background:#060607; }}'
    f'QPushButton[pad="true"]:checked {{ background:#0a2e4d; border-color:{COLOR_DECK_A}; color:{COLOR_WAVE_LIGHT}; }}'
    f'QComboBox {{ background:#17181d; border:1px solid #2a2c33; border-radius:3px; padding:6px 9px; color:{COLOR_TEXT}; }}'
    f'QComboBox:hover {{ border-color:#3a3d46; }}'
    f'QComboBox::drop-down {{ border:none; width:18px; }}'
    f'QComboBox QAbstractItemView {{ background:#17181d; color:{COLOR_TEXT}; border:1px solid #2a2c33; selection-background-color:#0a2e4d; }}'
    f'QSlider::groove:horizontal {{ height:8px; background:#060607; border:1px solid {COLOR_BORDER}; border-radius:2px; }}'
    f'QSlider::sub-page:horizontal {{ background:#3a3d46; border-radius:2px; }}'
    f'QSlider::handle:horizontal {{ width:14px; height:24px; margin:-9px 0; background:qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #d8d8d8, stop:0.44 #d8d8d8, stop:0.5 #1c1d1f, stop:0.56 #d8d8d8, stop:1 #d8d8d8); border:2px solid #000000; border-radius:2px; }}'
    f'QProgressBar {{ background:{COLOR_DISPLAY_BG}; border:1px solid {COLOR_BORDER}; border-radius:2px; min-height:12px; color:{COLOR_DISPLAY_FG}; font-family:{MONO_STACK}; }}'
    f'QProgressBar::chunk {{ background:qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1fa34a, stop:0.75 #2ee66b, stop:0.92 #ffc233, stop:1 #ff3838); border-radius:1px; }}'
    f'QLabel#status {{ background:{LCD_GRADIENT}; border:1px solid {COLOR_BORDER}; border-radius:2px; padding:8px 10px; color:{COLOR_DISPLAY_FG}; font-family:{MONO_STACK}; font-size:13px; }}'
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
