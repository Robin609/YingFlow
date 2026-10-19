"""Explicit voice selection; cloud billing is confirmed only at submission."""
from pathlib import Path
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QVBoxLayout, QFormLayout, QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox, QLabel, QDialogButtonBox, QPushButton, QStackedWidget, QWidget, QMessageBox
from ..render.speech import speech_options


def voice_picker(items, value):
    picker = QComboBox()
    picker.setEditable(True)
    for title, name in items:
        picker.addItem(title, name)
    index = picker.findData(value)
    if index < 0:
        picker.addItem(value, value)
        index = picker.count()-1
    picker.setCurrentIndex(index)
    return picker


def voice_value(picker):
    index = picker.currentIndex()
    return picker.itemData(index) if index >= 0 and picker.currentText() == picker.itemText(index) else picker.currentText().strip()


def speech_description(values):
    settings = speech_options(values)
    provider, voice = settings["provider"], settings["voice"]
    if provider == "google":
        return f"Google Cloud · {voice} · {settings['rate']:.2f} 倍"
    if provider == "edge":
        name = {"zh-CN-YunjianNeural": "云健", "zh-CN-YunxiNeural": "云希", "zh-CN-XiaoxiaoNeural": "晓晓"}.get(voice, voice)
        return f"Edge · {name} · 语速 {settings['rate']:+d}% · 音调 {settings['pitch']:+d} Hz"
    return f"Windows · {voice or '自动选择中文声音'} · 语速 {settings['rate']:+d}"


class VideoOptionsDialog(QDialog):
    def __init__(self, values, parent=None, *, speech_only=False):
        super().__init__(parent)
        self.setWindowTitle("配音设置" if speech_only else "视频设置")
        self.setMinimumWidth(650)
        layout = QVBoxLayout(self)
        heading = QLabel("设置默认配音" if speech_only else "技术动画／导入画面 + 中文配音 + 字幕")
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)
        hint = QLabel("保存后用于后续生成，已有视频不变。可先试听历史音色，保存设置不会发起在线配音。" if speech_only else
                      "成片时长跟随实际配音。声音按你选择的来源生成，失败时停下，不自动换成系统声音。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        form = QFormLayout()
        form.setSpacing(14)
        self.quality = QComboBox()
        self.quality.addItem("高清 1080 · 推荐", "1080")
        self.quality.addItem("预览 720 · 更快", "720")
        self.quality.setCurrentIndex(max(0, self.quality.findData(values.get("quality", "1080"))))
        self.provider = QComboBox()
        self.provider.setObjectName("speechProvider")
        for title, key in (("Google Cloud · WaveNet（旧工程后期）", "google"),
                           ("Edge 在线语音 · 云健／云希（旧工程早期）", "edge"),
                           ("Windows 系统语音 · 离线", "windows")):
            self.provider.addItem(title, key)
        if not speech_only:
            form.addRow("清晰度", self.quality)
        form.addRow("配音来源", self.provider)
        layout.addLayout(form)
        self.panels = QStackedWidget()
        layout.addWidget(self.panels)

        google = QWidget()
        gform = QFormLayout(google)
        self.google_voice = voice_picker([("WaveNet-B · 男声（旧工程默认）", "cmn-CN-Wavenet-B"),
                                          ("WaveNet-C · 男声", "cmn-CN-Wavenet-C")], values.get("google_voice") or "cmn-CN-Wavenet-B")
        self.google_rate = QDoubleSpinBox()
        self.google_rate.setRange(.25, 4)
        self.google_rate.setSingleStep(.05)
        self.google_rate.setDecimals(2)
        self.google_rate.setSuffix(" 倍")
        self.google_rate.setValue(values.get("google_rate", 1.10))
        self.google_project = QLineEdit(values.get("google_quota_project", ""))
        self.google_project.setPlaceholderText("本机 ADC 已有配额项目时可留空")
        gform.addRow("音色", self.google_voice)
        gform.addRow("语速", self.google_rate)
        gform.addRow("Google 项目 ID", self.google_project)
        info = QLabel("使用 Google Cloud 本机 ADC 授权；与 Gemini 网页登录分开。此服务可能计费，每次生成前单独确认。")
        info.setWordWrap(True)
        gform.addRow(info)
        setup = QPushButton("如何配置 Google 语音")
        setup.clicked.connect(self._google_setup)
        gform.addRow(setup)
        self.panels.addWidget(google)

        edge = QWidget()
        eform = QFormLayout(edge)
        self.edge_voice = voice_picker([("云健 · 沉稳男声", "zh-CN-YunjianNeural"),
                                       ("云希 · 青年男声", "zh-CN-YunxiNeural"),
                                       ("晓晓 · 女声", "zh-CN-XiaoxiaoNeural")], values.get("edge_voice") or "zh-CN-YunjianNeural")
        self.edge_rate = QSpinBox()
        self.edge_rate.setRange(-50, 100)
        self.edge_rate.setSuffix(" %")
        self.edge_rate.setValue(values.get("edge_rate", 10))
        self.edge_pitch = QSpinBox()
        self.edge_pitch.setRange(-50, 50)
        self.edge_pitch.setSuffix(" Hz")
        self.edge_pitch.setValue(values.get("edge_pitch", -5))
        eform.addRow("音色", self.edge_voice)
        eform.addRow("语速增减", self.edge_rate)
        eform.addRow("音调增减", self.edge_pitch)
        edge_hint = QLabel("需要联网。旧工程云健参数为语速 +10%、音调 -5 Hz；云希为 +10%、0 Hz。")
        edge_hint.setWordWrap(True)
        eform.addRow(edge_hint)
        self.panels.addWidget(edge)

        windows = QWidget()
        wform = QFormLayout(windows)
        self.rate = QSpinBox()
        self.rate.setRange(-3, 4)
        self.rate.setValue(values.get("rate", 1))
        self.rate.setToolTip("0 为系统正常语速；正数更快，负数更慢。")
        self.voice = QLineEdit(values.get("voice", ""))
        self.voice.setPlaceholderText("自动寻找这台电脑的中文语音")
        wform.addRow("语速", self.rate)
        wform.addRow("声音名称（可留空）", self.voice)
        system = QPushButton("管理 Windows 语音")
        system.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("ms-settings:speech")))
        wform.addRow(system)
        self.panels.addWidget(windows)
        self.provider.currentIndexChanged.connect(self.panels.setCurrentIndex)
        self.provider.setCurrentIndex(max(0, self.provider.findData(values.get("speech_provider", "windows"))))
        self.panels.setCurrentIndex(self.provider.currentIndex())
        preview = QPushButton("试听历史音色（本地样本，不发起生成）")
        preview.clicked.connect(self._preview_voice)
        layout.addWidget(preview)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存设置")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self):
        return {"quality": self.quality.currentData(), "rate": self.rate.value(), "voice": self.voice.text().strip(),
                "speech_provider": self.provider.currentData(), "edge_voice": voice_value(self.edge_voice),
                "edge_rate": self.edge_rate.value(), "edge_pitch": self.edge_pitch.value(),
                "google_voice": voice_value(self.google_voice), "google_rate": self.google_rate.value(),
                "google_quota_project": self.google_project.text().strip()}

    def _google_setup(self):
        QMessageBox.information(self, "配置 Google Cloud 语音",
                                "1. 在自己的 Google Cloud 项目启用 Cloud Text-to-Speech 和计费。\n"
                                "2. 通过官方 Google Cloud CLI 完成本机授权：\n"
                                "gcloud auth application-default login\n"
                                "3. 为 ADC 设置配额项目：\n"
                                "gcloud auth application-default set-quota-project 你的项目ID\n\n"
                                "也支持官方 GOOGLE_APPLICATION_CREDENTIALS 配置。凭据由 Google 工具管理，不填入 App 项目或打包文件。\n"
                                "仅保存这些设置不会生成语音或计费。")

    def _preview_voice(self):
        values = self.values()
        provider = values["speech_provider"]
        voice = values.get("google_voice" if provider == "google" else "edge_voice")
        files = {"cmn-CN-Wavenet-B": "google-wavenet-b.mp3", "zh-CN-YunjianNeural": "edge-yunjian.mp3", "zh-CN-YunxiNeural": "edge-yunxi.mp3"}
        name = files.get(voice) if provider != "windows" else None
        path = Path(__file__).resolve().parents[1] / "assets/speech" / name if name else None
        if not path or not path.is_file():
            QMessageBox.information(self, "历史试听", "该音色暂无随附的历史样本。试听按钮不会自动发起在线生成。")
            return
        from .preview import PreviewDialog
        self.preview = PreviewDialog(path, self)
        self.preview.setWindowTitle("历史音色试听 · " + voice)
        self.preview.show()
