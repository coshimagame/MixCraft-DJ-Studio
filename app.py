import os
import sys
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import sounddevice as sd
from PySide6.QtCore import Qt, QTimer, Signal, QObject
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QSlider, QFileDialog, QComboBox, QMessageBox, QFrame, QProgressBar, QScrollArea)
from engine import Engine, Journal, RATE, load_track
from audio_config import resolve_output

# ---------------------------------------------------------------------------
# UI palette — rugged industrial LCD panel. Near-black metal chassis with
# heavy bezels; every numeric / status readout is a monochrome amber LCD
# (dark glass, mono font, inset frame). Deck A orange / Deck B cyan survive
# only as small header markers so the amber display language stays in charge.
# ---------------------------------------------------------------------------
COLOR_BG = '#141518'          # chassis base (dark metal)
COLOR_PANEL = '#1e1f22'       # panel surface (decks / mixer band)
COLOR_PANEL_HI = '#26282c'    # raised surface (inputs, slider grooves)
COLOR_BUTTON = '#26282c'
COLOR_BUTTON_HOVER = '#313438'
COLOR_BORDER = '#3f4247'      # heavy bezel borders
COLOR_BORDER_HI = '#5a5e63'
COLOR_TEXT = '#eceae4'
COLOR_TEXT_SUB = '#a6a39b'
COLOR_TEXT_MUTE = '#716e68'
COLOR_DECK_A = '#ff7a1a'      # Deck A header marker only (orange)
COLOR_DECK_B = '#22b8e6'      # Deck B header marker only (cyan)
COLOR_LCD_BG = '#04070a'      # LCD glass (near-black, green cast)
COLOR_LCD_FG = '#ffb000'      # LCD amber
COLOR_LCD_DIM = '#c07f00'     # dimmer amber for secondary readouts
COLOR_METER = '#ffb000'       # level meter = LCD amber
COLOR_WAVE_BG = '#04070a'
COLOR_WAVE_GRID = '#16241a'   # dim LCD-green grid
FONT_STACK = '"Segoe UI", "Yu Gothic UI", "Meiryo UI", sans-serif'
MONO_STACK = 'Consolas, "DejaVu Sans Mono", "Courier New", monospace'
SECTION_LABEL_QSS = f'color:{COLOR_TEXT_MUTE};font-family:{MONO_STACK};font-size:11px;font-weight:700'
LCD_GRADIENT = ('qlineargradient(x1:0, y1:0, x2:0, y2:1, '
                'stop:0 #0a120b, stop:0.45 #04070a, stop:1 #060a06)')

def lcd_qss(size, bold=True, fg=None):
    """Inset amber-on-black LCD readout style for display-only labels."""
    return (f'background:{LCD_GRADIENT};color:{fg or COLOR_LCD_FG};'
            f'font-family:{MONO_STACK};font-size:{size}px;'
            f'font-weight:{700 if bold else 400};'
            'border:2px solid #000000;border-radius:2px;padding:4px 10px')

class Result(QObject):
    ready = Signal(int, object, str)

class Waveform(QWidget):
    seek = Signal(float)
    def __init__(self, color):
        super().__init__()
        self.track = None
        self.position = 0
        self.color = color
        self.setMinimumHeight(120)
    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(COLOR_WAVE_BG))
        p.setPen(QColor(COLOR_WAVE_GRID))
        for x in range(0, self.width(), 40): p.drawLine(x, 0, x, self.height())
        mid = self.height()/2
        p.drawLine(0, int(mid), self.width(), int(mid))
        if self.track:
            p.setPen(QPen(QColor(self.color), 1))
            for i, peak in enumerate(self.track.peaks):
                x = int(i*self.width()/len(self.track.peaks))
                y = int(peak*(mid-10))
                p.drawLine(x, int(mid)-y, x, int(mid)+y)
            x = int(self.position/max(1,len(self.track.audio))*self.width())
            p.setPen(QPen(QColor('#ffffff'), 2))
            p.drawLine(x, 0, x, self.height())
        else:
            p.setPen(QColor(COLOR_LCD_DIM))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, 'DROP AUDIO HERE  /  音声ファイルをドロップ')
    def mousePressEvent(self, event):
        if self.track: self.seek.emit(event.position().x()/self.width())

class Deck(QFrame):
    def __init__(self, window, index):
        super().__init__()
        self.window, self.index = window, index
        self.setAcceptDrops(True)
        self.setObjectName('deck')
        lay = QVBoxLayout(self)
        color = COLOR_DECK_A if index == 0 else COLOR_DECK_B
        slider_qss = (f'QSlider::groove:horizontal {{height:8px;background:#0c0d0e;border:1px solid {COLOR_BORDER};border-radius:2px}}'
                      f'QSlider::sub-page:horizontal {{background:{COLOR_LCD_DIM};border-radius:2px}}'
                      f'QSlider::handle:horizontal {{width:14px;height:24px;margin:-9px 0;background:qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #d8d8d8, stop:0.44 #d8d8d8, stop:0.5 #1c1d1f, stop:0.56 #d8d8d8, stop:1 #d8d8d8);border:2px solid #000000;border-radius:2px}}')
        self.slider_qss = slider_qss
        top = QHBoxLayout()
        label = QLabel('DECK '+('A' if index == 0 else 'B'))
        label.setStyleSheet(f'color:{COLOR_TEXT};background:{COLOR_PANEL_HI};font-size:16px;font-weight:800;padding:5px 12px 5px 8px;border:2px solid {COLOR_BORDER};border-left:8px solid {color};border-radius:2px')
        top.addWidget(label)
        top.addStretch()
        self.bpm = QLabel('— BPM')
        self.bpm.setStyleSheet(lcd_qss(26))
        top.addWidget(self.bpm)
        lay.addLayout(top)
        self.name = QLabel('No track loaded / 未読込')
        self.name.setStyleSheet(lcd_qss(12, bold=False, fg=COLOR_LCD_DIM))
        self.name.setWordWrap(True)
        lay.addWidget(self.name)
        self.wave = Waveform(COLOR_LCD_FG)
        self.wave.seek.connect(lambda x: window.engine.command('seek', index, int(x*len(self.wave.track.audio))))
        lay.addWidget(self.wave)
        self.clock = QLabel('00:00 / 00:00')
        self.clock.setStyleSheet(lcd_qss(15))
        lay.addWidget(self.clock)
        row = QHBoxLayout()
        self.load = QPushButton('Load / 読込')
        self.load.clicked.connect(self.choose)
        self.play = QPushButton('▶ Play / 再生')
        self.play.clicked.connect(lambda: window.engine.command('play', index, not window.engine.decks[index]['playing']))
        self.cue = QPushButton('■ Reset / 先頭')
        self.cue.clicked.connect(self.reset)
        for button in [self.load, self.play, self.cue]: row.addWidget(button)
        lay.addLayout(row)
        volume_label = QLabel('CHANNEL VOLUME')
        volume_label.setStyleSheet(SECTION_LABEL_QSS)
        lay.addWidget(volume_label)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setStyleSheet(slider_qss)
        slider.setValue(80)
        slider.setRange(0,100)
        slider.setValue(80)
        slider.valueChanged.connect(lambda v: window.engine.command('volume',index,v/100))
        lay.addWidget(slider)
        # Phase 2: GAIN / 3-band EQ / FILTER (centre = neutral)
        self.controls = {}
        for label, action, default in [('GAIN', 'gain', 50), ('EQ HIGH', 'eq_high', 50),
                                       ('EQ MID', 'eq_mid', 50), ('EQ LOW', 'eq_low', 50),
                                       ('FILTER', 'filter', 50)]:
            control_row = QHBoxLayout()
            control_label = QLabel(label)
            control_label.setStyleSheet(SECTION_LABEL_QSS)
            control_label.setFixedWidth(80)
            control_row.addWidget(control_label)
            s = QSlider(Qt.Orientation.Horizontal)
            s.setStyleSheet(slider_qss)
            s.setRange(0, 100); s.setValue(default)
            if action == 'gain':
                s.valueChanged.connect(lambda v, i=index: window.engine.command('gain', i, v/50))
            else:
                s.valueChanged.connect(lambda v, a=action, i=index: window.engine.command(a, i, v/100))
            self.controls[action] = s
            control_row.addWidget(s)
            lay.addLayout(control_row)
        # Phase 2: CUE / HOT CUE / LOOP / QUANTIZE / SYNC
        row2 = QHBoxLayout()
        cue_set = QPushButton('Cue Set'); cue_set.clicked.connect(lambda: window.engine.command('cue_set', index))
        cue_jump = QPushButton('Cue'); cue_jump.clicked.connect(lambda: window.engine.command('cue_jump', index))
        hot1 = QPushButton('Hot 1'); hot1.clicked.connect(lambda: window.engine.command('hotcue_jump', index, 1))
        hot1s = QPushButton('Hot1 Set'); hot1s.clicked.connect(lambda: window.engine.command('hotcue_set', index, 1))
        for b in [cue_set, cue_jump, hot1s, hot1]: row2.addWidget(b)
        lay.addLayout(row2)
        row3 = QHBoxLayout()
        loop_in = QPushButton('Loop In'); loop_in.clicked.connect(lambda: window.engine.command('loop_in', index))
        loop_out = QPushButton('Loop Out'); loop_out.clicked.connect(lambda: window.engine.command('loop_out', index))
        loop_off = QPushButton('Loop Exit'); loop_off.clicked.connect(lambda: window.engine.command('loop_exit', index))
        quant = QPushButton('Quantize'); quant.setCheckable(True)
        self.quant = quant
        quant.toggled.connect(lambda on, i=index: window.engine.command('quantize', i, on))
        sync = QPushButton('SYNC'); sync.setCheckable(True)
        self.sync = sync
        sync.setToolTip('Tempo match only / テンポのみ一致。音程も変わります。ビート位相同期なし。')
        sync.toggled.connect(lambda on, i=index: window.engine.command('sync', i, on))
        for b in [loop_in, loop_out, loop_off, quant, sync]: row3.addWidget(b)
        lay.addLayout(row3)
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
        self.setWindowTitle('MixCraft DJ Studio • Mw-fActory • Phase 2 Trial')
        self.resize(1100,850)
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
        data = Path(os.getenv('LOCALAPPDATA', str(Path.home()/'.local/share'))) / 'MixCraftDJStudio'
        self.journal = Journal(data/'sessions'/f'{time.time_ns()}.jsonl')
        root = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(root)
        self.setCentralWidget(scroll)
        layout = QVBoxLayout(root)
        header = QHBoxLayout()
        title = QLabel(f'MIXCRAFT  <span style="color:{COLOR_LCD_FG}">DJ STUDIO</span>')
        title.setStyleSheet('font-size:26px;font-weight:800')
        header.addWidget(title)
        header.addStretch()
        brand = QLabel('MW-FACTORY   /   OFFLINE   /   v0.2.0-trial')
        brand.setStyleSheet(f'color:{COLOR_TEXT_MUTE};font-family:{MONO_STACK};font-size:12px')
        header.addWidget(brand)
        layout.addLayout(header)
        mode_label = QLabel('DJ MODE     •     2 DECK PERFORMANCE                                        PHASE 2 / 試遊版')
        mode_label.setStyleSheet(f'color:{COLOR_TEXT_SUB};font-size:12px;font-weight:600')
        layout.addWidget(mode_label)
        decks = QHBoxLayout()
        self.decks = [Deck(self,i) for i in range(2)]
        for deck in self.decks: decks.addWidget(deck)
        layout.addLayout(decks)
        mixer = QFrame()
        mixer.setObjectName('mixer')
        mix = QHBoxLayout(mixer)
        deck_a_label = QLabel('A')
        deck_a_label.setStyleSheet(f'color:{COLOR_DECK_A};font-size:15px;font-weight:800')
        mix.addWidget(deck_a_label)
        self.cross = QSlider(Qt.Orientation.Horizontal)
        self.cross.setRange(0,1000)
        self.cross.setValue(500)
        self.cross.valueChanged.connect(lambda v:self.engine.command('cross',value=v/1000))
        mix.addWidget(self.cross,3)
        deck_b_label = QLabel(f'<span style="color:{COLOR_DECK_B};font-weight:800">B</span>      MASTER')
        deck_b_label.setStyleSheet('font-size:15px')
        mix.addWidget(deck_b_label)
        master = QSlider(Qt.Orientation.Horizontal)
        master.setRange(0,100)
        master.setValue(80)
        master.valueChanged.connect(lambda v:self.engine.command('master',value=v/100))
        mix.addWidget(master)
        self.meter = QProgressBar()
        self.meter.setRange(0,100)
        self.meter.setMaximumWidth(130)
        mix.addWidget(self.meter)
        layout.addWidget(mixer)
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
        deck.bpm.setText(f'{track.bpm or "—"} BPM ≈')
        deck.bpm.setToolTip(f'Estimate confidence: {track.confidence:.0%}. Half/double tempo errors possible.')
        key_text = f' • Key {track.key} ({track.camelot})' if track.key else ''
        deck.name.setText(Path(track.path).name + key_text)
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
            def clock(s): return f'{int(s)//60:02}:{int(s)%60:02}'
            widget.clock.setText(f'{clock(d["position"]/RATE)} / {clock(length)}')
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
    f'QWidget {{ background:{COLOR_BG}; color:{COLOR_TEXT}; font-family:{FONT_STACK}; font-size:13px; }}'
    f'QMainWindow {{ background:{COLOR_BG}; }}'
    f'QScrollArea {{ background:{COLOR_BG}; border:none; }}'
    f'QLabel {{ background:transparent; }}'
    f'QFrame#deck {{ background:qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #232428, stop:1 #1a1b1e); border:2px solid {COLOR_BORDER_HI}; border-radius:3px; padding:12px; }}'
    f'QFrame#mixer {{ background:qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #232428, stop:1 #1a1b1e); border:2px solid {COLOR_BORDER_HI}; border-radius:3px; padding:10px 12px; }}'
    f'QPushButton {{ background:qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #2b2d30, stop:1 #222427); border:2px solid {COLOR_BORDER_HI}; border-radius:3px; padding:8px 10px; font-weight:600; }}'
    f'QPushButton:hover {{ background:{COLOR_BUTTON_HOVER}; border-color:#6d7176; }}'
    f'QPushButton:pressed {{ background:#17181a; }}'
    f'QPushButton:disabled {{ background:#1b1c1e; border-color:{COLOR_BORDER}; color:#5d5d68; }}'
    f'QPushButton:checked {{ background:#3d2c04; border-color:{COLOR_LCD_FG}; color:{COLOR_LCD_FG}; font-weight:800; }}'
    f'QComboBox {{ background:{COLOR_PANEL_HI}; border:2px solid {COLOR_BORDER_HI}; border-radius:3px; padding:7px 9px; }}'
    f'QComboBox:hover {{ border-color:#6d7176; }}'
    f'QComboBox QAbstractItemView {{ background:{COLOR_PANEL_HI}; color:{COLOR_TEXT}; border:1px solid {COLOR_BORDER_HI}; selection-background-color:{COLOR_BUTTON_HOVER}; }}'
    f'QSlider::groove:horizontal {{ height:8px; background:#0c0d0e; border:1px solid {COLOR_BORDER}; border-radius:2px; }}'
    f'QSlider::sub-page:horizontal {{ background:{COLOR_LCD_DIM}; border-radius:2px; }}'
    f'QSlider::handle:horizontal {{ width:14px; height:24px; margin:-9px 0; background:qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #d8d8d8, stop:0.44 #d8d8d8, stop:0.5 #1c1d1f, stop:0.56 #d8d8d8, stop:1 #d8d8d8); border:2px solid #000000; border-radius:2px; }}'
    f'QProgressBar {{ background:{COLOR_LCD_BG}; border:2px solid #000000; border-radius:2px; min-height:14px; color:{COLOR_LCD_FG}; font-family:{MONO_STACK}; }}'
    f'QProgressBar::chunk {{ background:qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ffc233, stop:1 #cc8a00); border-radius:1px; }}'
    f'QLabel#status {{ background:{LCD_GRADIENT}; border:2px solid #000000; border-radius:2px; padding:8px 10px; color:{COLOR_LCD_FG}; font-family:{MONO_STACK}; font-size:13px; }}'
    f'QToolTip {{ background:{COLOR_PANEL_HI}; color:{COLOR_TEXT}; border:1px solid {COLOR_BORDER_HI}; padding:4px 6px; }}'
    f'QScrollBar:vertical {{ background:{COLOR_BG}; width:12px; margin:0; }}'
    f'QScrollBar::handle:vertical {{ background:#3a3d40; border-radius:2px; min-height:30px; }}'
    f'QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}'
    f'QScrollBar:horizontal {{ background:{COLOR_BG}; height:12px; margin:0; }}'
    f'QScrollBar::handle:horizontal {{ background:#3a3d40; border-radius:2px; min-width:30px; }}'
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
