"""Local browser discovery and official-login guidance, without model calls."""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QPushButton,
    QLineEdit, QFileDialog, QWidget, QCheckBox, QSpinBox,
)

from ..config import browser_setup_defaults


def text_label(text, name=None):
    label = QLabel(text)
    label.setWordWrap(True)
    if name:
        label.setObjectName(name)
    return label


class BrowserConnectionWizard(QDialog):
    def __init__(self, engine, tasks, parent=None):
        super().__init__(parent)
        self.engine, self.tasks = engine, tasks
        self.pending_id = None
        self.result = None
        self.setWindowTitle("Gemini 连接助手")
        self.setObjectName("browserConnectionWizard")
        self.setMinimumWidth(660)
        self.resize(720, 540)
        self.setModal(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 24)
        layout.setSpacing(16)
        layout.addWidget(text_label("把 Gemini 连接到工作台", "pageTitle"))
        layout.addWidget(text_label("工作台自动寻找本机 Chrome、创建专用目录并避开已占用端口。换电脑后，在这里重新配置并登录。", "muted"))
        self.discovery = text_label("", "hint")
        layout.addWidget(self.discovery)
        layout.addWidget(text_label("打开专用窗口 → 在 Chrome 中登录 → 回这里检查连接", "sectionTitle"))
        layout.addWidget(text_label("账号和验证码只在 Google 官方页面输入。检查连接不会发送文稿。", "muted"))
        self.status = text_label("", "statusLine")
        self.status.setObjectName("statusLine")
        self.status.setMinimumHeight(64)
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        self.open_button = QPushButton("打开登录窗口")
        self.open_button.setObjectName("primary")
        self.open_button.clicked.connect(lambda: self._connect(True))
        self.check_button = QPushButton("我已登录，检查连接")
        self.check_button.setObjectName("checkBrowserConnection")
        self.check_button.clicked.connect(lambda: self._connect(False))
        actions.addWidget(self.open_button)
        actions.addWidget(self.check_button)
        layout.addLayout(actions)
        self.options_button = QPushButton("其他连接选项")
        self.options_button.setCheckable(True)
        layout.addWidget(self.options_button, alignment=Qt.AlignmentFlag.AlignLeft)
        self.options = QWidget()
        form = QFormLayout(self.options)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(12)
        self.chrome = QLineEdit()
        self.chrome.setReadOnly(True)
        self.chrome.setPlaceholderText("未找到 Chrome，请安装或选择本机 chrome.exe")
        choose_chrome = QPushButton("选择…")
        choose_chrome.clicked.connect(self._choose_chrome)
        chrome_row = QHBoxLayout()
        chrome_row.addWidget(self.chrome, 1)
        chrome_row.addWidget(choose_chrome)
        form.addRow("Chrome", chrome_row)
        self.profile = QLineEdit()
        self.profile.setReadOnly(True)
        self.profile.setToolTip("由工作台自动创建，不需要手动建立文件夹。")
        form.addRow("专用目录", self.profile)
        self.mode = QLineEdit()
        self.mode.setPlaceholderText("网页菜单中的准确名称，例如 4 Argon")
        self.mode.textChanged.connect(self._changed)
        form.addRow("审核模式", self.mode)
        profile_row = QHBoxLayout()
        self.detect_button = QPushButton("重新查找本机 Chrome")
        self.detect_button.clicked.connect(self._detect)
        existing = QPushButton("复用已有专用目录…")
        existing.clicked.connect(self._choose_profile)
        fresh = QPushButton("使用新专用窗口")
        fresh.setToolTip("保留原目录和登录资料，新窗口需要重新登录。")
        fresh.clicked.connect(self._fresh_profile)
        for control in (self.detect_button, existing, fresh):
            profile_row.addWidget(control)
        form.addRow(profile_row)
        self.auto_port = QCheckBox("自动选择可用端口（推荐）")
        self.auto_port.toggled.connect(self._port_mode)
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.valueChanged.connect(self._changed)
        port_row = QHBoxLayout()
        port_row.addWidget(self.auto_port)
        port_row.addWidget(self.port)
        form.addRow("连接端口", port_row)
        layout.addWidget(self.options)
        self.options.hide()
        self.options_button.toggled.connect(self.options.setVisible)
        self.chrome.textChanged.connect(self._changed)
        self.profile.textChanged.connect(self._changed)
        layout.addStretch()
        footer = QHBoxLayout()
        footer.addStretch()
        self.close_button = QPushButton("稍后再说")
        self.close_button.clicked.connect(self.reject)
        self.finish_button = QPushButton("完成配置")
        self.finish_button.setObjectName("primary")
        self.finish_button.clicked.connect(self.accept)
        footer.addWidget(self.close_button)
        footer.addWidget(self.finish_button)
        layout.addLayout(footer)
        self._apply(browser_setup_defaults(engine.root, engine.get_settings()))

    def _apply(self, settings):
        self.chrome.setText(settings.get("chrome_executable") or "")
        self.profile.setText(settings["gemini_profile_dir"])
        self.mode.setText(settings["gemini_mode"])
        self.port.setValue(settings.get("browser_port") or 9222)
        self.auto_port.setChecked(settings.get("browser_auto_port", True))
        self.port.setEnabled(not self.auto_port.isChecked())
        self._changed()

    def _changed(self, *_):
        self.result = None
        self.finish_button.setEnabled(False)
        found = bool(self.chrome.text() and Path(self.chrome.text()).is_file())
        self.discovery.setText("已找到本机 Chrome · 使用 App 专用窗口，不接管日常浏览器。" if found else "没有找到 Chrome。请先安装 Chrome，或展开其他连接选项选择本机程序。")
        self.open_button.setEnabled(found and not self.pending_id)
        self.check_button.setEnabled(found and not self.pending_id)
        self.status.setText("点击“打开登录窗口”；完成登录后回到这里检查连接。")

    def _port_mode(self, automatic):
        self.port.setEnabled(not automatic)
        self._changed()

    def _detect(self):
        values = browser_setup_defaults(self.engine.root, {**self.engine.get_settings(), "chrome_executable": None})
        self.chrome.setText(values.get("chrome_executable") or "")
        self._changed()

    def _choose_chrome(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择本机 Chrome", "", "Chrome (chrome.exe)")
        if path:
            self.chrome.setText(path)

    def _choose_profile(self):
        path = QFileDialog.getExistingDirectory(self, "选择原 App 专用目录，不要选择日常 Chrome 目录")
        if path:
            self.profile.setText(path)

    def _fresh_profile(self):
        self.profile.setText(str((self.engine.root / "browser" / ("gemini-" + uuid4().hex[:8])).resolve()))
        self.auto_port.setChecked(True)
        self.status.setText("将自动创建新的本机专用窗口；原目录保留。点击“打开登录窗口”后重新登录。")

    def _connect(self, launch):
        if self.pending_id:
            return
        settings = {"chrome_executable": self.chrome.text(), "gemini_profile_dir": self.profile.text(),
                    "browser_port": self.port.value(), "browser_auto_port": self.auto_port.isChecked(),
                    "gemini_mode": self.mode.text().strip() or "4 Argon"}
        self.pending_id = self.tasks.configure_browser(settings, launch=launch)
        self.result = None
        self.finish_button.setEnabled(False)
        self.open_button.setEnabled(False)
        self.check_button.setEnabled(False)
        self.options.setEnabled(False)
        self.status.setText("正在等待后台检查本机连接…" if not launch else "正在打开或复用专用窗口；端口冲突时自动避让…")

    def handle_event(self, event):
        if not self.pending_id or event.get("request_id") != self.pending_id:
            return
        self.pending_id = None
        self.options.setEnabled(True)
        self.open_button.setEnabled(True)
        self.check_button.setEnabled(True)
        if event.get("error"):
            self.status.setText(event["message"])
            self.options_button.setChecked(True)
            return
        result = event["result"]
        self.port.blockSignals(True)
        self.port.setValue(result["port"])
        self.port.blockSignals(False)
        self.result = result
        ready = result.get("auth_observed") == "sign_in_not_visible" and result.get("mode_verified", False)
        self.finish_button.setEnabled(ready)
        if ready:
            self.status.setText(f"连接就绪 · {result['mode_selected']} · 本机端口 {result['port']}\n已核对登录页面和审核模式，可以回到工作台继续创作。")
        elif result["status"] == "not_running":
            self.status.setText("还没有可核对的专用窗口，请先点击“打开登录窗口”。")
        else:
            self.status.setText("专用窗口已连接。请在 Chrome 中完成 Gemini 登录，打开 Gemini 对话页，然后回这里点击“我已登录，检查连接”。")
