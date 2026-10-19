"""In-app local media preview; never uploads assets or changes source files."""
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, QSize
from PySide6.QtGui import QPixmap, QImageReader
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSlider


class PreviewDialog(QDialog):
    def __init__(self, path: Path, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"素材预览 · {path.name}")
        self.resize(960, 760)
        layout = QVBoxLayout(self)
        self.canvas = QLabel("正在读取本地媒体…")
        self.canvas.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.canvas.setMinimumSize(480, 360)
        self.canvas.setStyleSheet("background:#080b0f;color:#b1bdce;")
        layout.addWidget(self.canvas, 1)
        controls = QHBoxLayout()
        self.play_button = QPushButton("播放")
        self.play_button.clicked.connect(self.toggle)
        controls.addWidget(self.play_button)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.sliderMoved.connect(lambda value: self.player.setPosition(value))
        controls.addWidget(self.slider, 1)
        self.time = QLabel("00:00 / 00:00")
        controls.addWidget(self.time)
        layout.addLayout(controls)
        filename = QLabel(str(path))
        filename.setWordWrap(True)
        layout.addWidget(filename)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.7)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio)
        self.sink = QVideoSink(self)
        self.sink.videoFrameChanged.connect(self._frame)
        self.player.setVideoSink(self.sink)
        self.player.positionChanged.connect(self._position)
        self.player.durationChanged.connect(lambda duration: self.slider.setRange(0, duration))
        self.player.playbackStateChanged.connect(lambda state: self.play_button.setText("暂停" if state == QMediaPlayer.PlaybackState.PlayingState else "播放"))
        self.player.errorOccurred.connect(lambda _, message: self.canvas.setText(f"无法预览：{message}"))
        self.last_image = None
        image_reader = QImageReader(str(path.resolve()))
        image_reader.setAutoTransform(True)
        if image_reader.canRead() and image_reader.imageCount() <= 1:
            image_reader.setScaledSize(image_reader.size().scaled(QSize(1920, 1080), Qt.AspectRatioMode.KeepAspectRatio))
            self.last_image = image_reader.read()
            if self.last_image.isNull():
                self.canvas.setText("无法预览：图片解码失败。")
            else:
                self._paint_frame()
            self.play_button.hide()
            self.slider.hide()
            self.time.setText("静态图片")
        else:
            self.player.setSource(QUrl.fromLocalFile(str(path.resolve())))
            if path.suffix.lower() in {".mp3", ".wav", ".aac", ".m4a", ".flac", ".ogg"}:
                self.canvas.setMinimumSize(480, 100)
                self.canvas.setText("配音试听\n点击“播放”")
                self.resize(720, 300)

    def toggle(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    @staticmethod
    def _clock(ms):
        return f"{ms // 60000:02d}:{ms // 1000 % 60:02d}"

    def _position(self, position):
        if not self.slider.isSliderDown():
            self.slider.setValue(position)
        self.time.setText(f"{self._clock(position)} / {self._clock(self.player.duration())}")

    def _frame(self, frame):
        image = frame.toImage()
        if not image.isNull():
            self.last_image = image
            self._paint_frame()

    def _paint_frame(self):
        if self.last_image is not None and not self.last_image.isNull():
            self.canvas.setPixmap(QPixmap.fromImage(self.last_image).scaled(
                self.canvas.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "last_image"):
            self._paint_frame()

    def closeEvent(self, event):
        self.player.stop()
        super().closeEvent(event)
