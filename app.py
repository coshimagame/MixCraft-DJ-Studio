import os
import sys
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import sounddevice as sd
from PySide6.QtCore import Qt, QTimer, Signal, QObject
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QSlider, QFileDialog, QComboBox, QMessageBox, QFrame, QProgressBar)
from engine import Engine, Journal, RATE, load_track

class Result(QObject):
    ready = Signal(int, object, str)

class Waveform(QWidget):
    seek = Signal(float)
    def __init__(self, color):
        super().__init__()
        self.track = None
        self.position = 0
        self.color = color
        self.setMinimumHeight(170)
    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor('#10151f'))
        p.setPen(QColor('#253043'))
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
            p.setPen(QColor('#8391a8'))
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
        color = '#55d9cf' if index == 0 else '#b69bff'
        top = QHBoxLayout()
        label = QLabel('DECK '+('A' if index == 0 else 'B'))
        label.setStyleSheet(f'color:{color};font-size:23px;font-weight:700')
        top.addWidget(label)
        top.addStretch()
        self.bpm = QLabel('— BPM')
        self.bpm.setStyleSheet('font-size:25px')
        top.addWidget(self.bpm)
        lay.addLayout(top)
        self.name = QLabel('No track loaded / 未読込')
        self.name.setWordWrap(True)
        lay.addWidget(self.name)
        self.wave = Waveform(color)
        self.wave.seek.connect(lambda x: window.engine.command('seek', index, int(x*len(self.wave.track.audio))))
        lay.addWidget(self.wave)
        self.clock = QLabel('00:00 / 00:00')
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
        lay.addWidget(QLabel('CHANNEL VOLUME'))
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setValue(80)
        slider.setRange(0,100)
        slider.setValue(80)
        slider.valueChanged.connect(lambda v: window.engine.command('volume',index,v/100))
        lay.addWidget(slider)
        # Phase 2: GAIN / 3-band EQ / FILTER (centre = neutral)
        for label, action, default in [('GAIN', 'gain', 50), ('EQ HIGH', 'eq_high', 50),
                                       ('EQ MID', 'eq_mid', 50), ('EQ LOW', 'eq_low', 50),
                                       ('FILTER', 'filter', 50)]:
            lay.addWidget(QLabel(label))
            s = QSlider(Qt.Orientation.Horizontal)
            s.setRange(0, 100); s.setValue(default)
            if action == 'gain':
                s.valueChanged.connect(lambda v, i=index: window.engine.command('gain', i, v/50))
            else:
                s.valueChanged.connect(lambda v, a=action, i=index: window.engine.command(a, i, v/100))
            lay.addWidget(s)
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
        quant.toggled.connect(lambda on, i=index: window.engine.command('quantize', i, on))
        sync = QPushButton('SYNC'); sync.setCheckable(True)
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
        self.setWindowTitle('MixCraft DJ Studio • Mw-fActory • Phase 1')
        self.resize(1100,740)
        self.engine = Engine()
        self.stream = None
        self.pool = ThreadPoolExecutor(max_workers=2)
        self.result = Result()
        self.result.ready.connect(self.loaded)
        self.loading = set()
        data = Path(os.getenv('LOCALAPPDATA', str(Path.home()/'.local/share'))) / 'MixCraftDJStudio'
        self.journal = Journal(data/'sessions'/f'{time.time_ns()}.jsonl')
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        header = QHBoxLayout()
        title = QLabel('MIXCRAFT  <span style="color:#55d9cf">DJ STUDIO</span>')
        title.setStyleSheet('font-size:27px;font-weight:800')
        header.addWidget(title)
        header.addStretch()
        header.addWidget(QLabel('MW-FACTORY   /   OFFLINE   /   v0.1.0'))
        layout.addLayout(header)
        layout.addWidget(QLabel('DJ MODE     •     2 DECK PERFORMANCE                                        PHASE 1 / 試遊版'))
        decks = QHBoxLayout()
        self.decks = [Deck(self,i) for i in range(2)]
        for deck in self.decks: decks.addWidget(deck)
        layout.addLayout(decks)
        mix = QHBoxLayout()
        mix.addWidget(QLabel('A'))
        self.cross = QSlider(Qt.Orientation.Horizontal)
        self.cross.setRange(0,1000)
        self.cross.setValue(500)
        self.cross.valueChanged.connect(lambda v:self.engine.command('cross',value=v/1000))
        mix.addWidget(self.cross,3)
        mix.addWidget(QLabel('B      MASTER'))
        master = QSlider(Qt.Orientation.Horizontal)
        master.setRange(0,100)
        master.setValue(80)
        master.valueChanged.connect(lambda v:self.engine.command('master',value=v/100))
        mix.addWidget(master)
        self.meter = QProgressBar()
        self.meter.setRange(0,100)
        self.meter.setMaximumWidth(130)
        mix.addWidget(self.meter)
        layout.addLayout(mix)
        layout.addWidget(QLabel('TRANSITION AUDITION / ミックス試聴  —  Aの末尾 → Bの先頭（テンポ同期なし）'))
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
        settings.addWidget(QLabel('AUDIO OUTPUT / 出力'))
        self.devices = QComboBox()
        self.devices.addItem('System default / 既定', None)
        try:
            for i,d in enumerate(sd.query_devices()):
                if d['max_output_channels'] >= 2: self.devices.addItem(d['name'],i)
        except Exception: pass
        settings.addWidget(self.devices,1)
        apply = QPushButton('Apply / 適用')
        apply.clicked.connect(self.start_audio)
        settings.addWidget(apply)
        layout.addLayout(settings)
        self.status = QLabel('Ready / 準備完了')
        layout.addWidget(self.status)
        layout.addWidget(QLabel('BPM・キーは推定です（手動修正は今後拡張） • EQ/Filter/Cue/HotCue/Loop/Quantize/SYNCはPhase 2実装 • ALS互換は未確認。元ファイルは変更しません。'))
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
            hostapis = sd.query_hostapis()
            dev_info = sd.query_devices(device) if device is not None else sd.query_devices(kind='output')
            hostapi_name = hostapis[dev_info['hostapi']]['name']
            if 'WASAPI' in hostapi_name:
                extra = sd.WasapiSettings(exclusive=False)
        except Exception:
            extra = None
        try:
            kwargs = dict(samplerate=RATE, channels=2, blocksize=512, latency='low',
                dtype='float32', device=device, callback=self.callback)
            if extra is not None: kwargs['extra_settings'] = extra
            self.stream = sd.OutputStream(**kwargs)
            self.stream.start()
            latency_ms = (self.stream.latency or 0) * 1000
            self.status.setText(f'Audio active • 48 kHz / 512 frames • {hostapi_name or "default host"} • latency {latency_ms:.0f} ms (報告値) • 操作を自動保存中')
        except Exception as error:
            self.status.setText(f'Audio unavailable / 出力エラー: {error}')
    def callback(self,outdata,frames,time_info,status):
        if status: self.engine.overloads += 1
        outdata[:] = self.engine.render(frames)
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
            widget.wave.position = d['position']
            widget.wave.update()
            widget.play.setText('Ⅱ Pause / 一時停止' if d['playing'] else '▶ Play / 再生')
            length = len(d['track'].audio)/RATE if d['track'] else 0
            def clock(s): return f'{int(s)//60:02}:{int(s)%60:02}'
            widget.clock.setText(f'{clock(d["position"]/RATE)} / {clock(length)}')
        self.meter.setValue(int(self.engine.peak*100))
        self.meter.setToolTip(f'Clamp / audio status count: {self.engine.overloads}')
        self.cross.setEnabled(self.engine.preview is None)
        self.preview_button.setEnabled(self.engine.preview is None)
    def closeEvent(self,event):
        self.timer.stop()
        self.pool.shutdown(wait=True)
        if self.stream: self.stream.stop(); self.stream.close()
        self.journal.drain(self.engine)
        self.journal.file.close()
        event.accept()

STYLE = '''
QWidget { background:#171d28; color:#e4eaf4; font-family:"Segoe UI"; font-size:13px; }
QMainWindow { background:#111722; } QFrame#deck { border:1px solid #303b4d; border-radius:10px; padding:12px; }
QPushButton { background:#2b374c; border:1px solid #40506a; border-radius:5px; padding:9px; }
QPushButton:hover { background:#3b4c63; } QPushButton:disabled { color:#6c7789; }
QComboBox { background:#253044; padding:8px; } QSlider::groove:horizontal { height:5px; background:#35425a; }
QSlider::handle:horizontal { width:16px; margin:-7px 0; background:#6bded3; border-radius:4px; }
QProgressBar { background:#10151f; border:0; } QProgressBar::chunk { background:#55d9cf; }
'''

if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    smoke_mode = len(sys.argv)>2 and sys.argv[1] in ('--smoke-test','--audio-smoke-test')
    window = Window(audio=not smoke_mode or sys.argv[1]=='--audio-smoke-test')
    window.show()
    if smoke_mode:
        from smoke import schedule
        schedule(app,window,sys.argv[2],sys.argv[1]=='--audio-smoke-test')
    else:
        for i,path in enumerate(sys.argv[1:3]): window.load(i,path)
    sys.exit(app.exec())
