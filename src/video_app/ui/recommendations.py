"""The daily home surface: real suggestions first, no project setup ceremony."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QPushButton, QComboBox, QFrame, QScrollArea, QProgressBar, QDialog, QFormLayout,
    QLineEdit, QDoubleSpinBox, QDialogButtonBox)


def text(value, name=None):
    widget = QLabel(value)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(True)
    if name:
        widget.setObjectName(name)
    return widget


class RecommendationPreferences(QDialog):
    def __init__(self, brief, parent=None):
        super().__init__(parent)
        self.setWindowTitle("推荐偏好")
        self.setMinimumWidth(500)
        self.original = brief
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.domain = QLineEdit(brief["domain"])
        self.audience = QLineEdit(brief["audience"])
        self.duration = QDoubleSpinBox()
        self.duration.setRange(1, 3600)
        self.duration.setDecimals(3)
        self.duration.setSuffix(" 秒")
        self.duration.setValue(brief["target_ms"] / 1000)
        self.aspect = QComboBox()
        self.aspect.addItems(["4:3", "16:9", "9:16", "1:1"])
        self.aspect.setCurrentText(brief["aspect"])
        for title, field in (("内容方向", self.domain), ("目标受众", self.audience),
                             ("参考时长", self.duration), ("画幅", self.aspect)):
            form.addRow(title, field)
        layout.addLayout(form)
        layout.addWidget(text("保存后更新本日推荐，不改动已经开始的作品。", "muted"))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存并更新推荐")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self):
        return {**self.original, "domain": self.domain.text().strip(), "audience": self.audience.text().strip(),
                "target_ms": round(self.duration.value() * 1000), "aspect": self.aspect.currentText()}


class RecommendationsPage(QWidget):
    requested = Signal()
    chosen = Signal(str, str, str)
    daySelected = Signal(str)
    preferencesRequested = Signal()
    folderRequested = Signal()
    detailsRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.render_key = None
        self.day = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)
        toolbar = QHBoxLayout()
        self.date_picker = QComboBox()
        self.date_picker.setObjectName("recommendationDate")
        self.date_picker.setMinimumWidth(180)
        self.date_picker.currentIndexChanged.connect(lambda: self.daySelected.emit(self.date_picker.currentData() or ""))
        toolbar.addWidget(self.date_picker)
        toolbar.addStretch()
        self.preferences = QPushButton("推荐偏好")
        self.preferences.clicked.connect(self.preferencesRequested)
        toolbar.addWidget(self.preferences)
        self.generate = QPushButton("更新推荐")
        self.generate.setObjectName("generateRecommendations")
        self.generate.clicked.connect(self.requested)
        toolbar.addWidget(self.generate)
        layout.addLayout(toolbar)
        self.scope = text("", "muted")
        layout.addWidget(self.scope)
        self.status = text("", "recommendationStatus")
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        container = QWidget()
        self.cards = QGridLayout(container)
        self.cards.setContentsMargins(0, 0, 4, 0)
        self.cards.setSpacing(16)
        self.scroll.setWidget(container)
        layout.addWidget(self.scroll, 1)
        self.empty = QFrame()
        self.empty.setObjectName("recommendationEmpty")
        empty_layout = QVBoxLayout(self.empty)
        empty_layout.addStretch()
        self.empty_title = text("正在准备每日推荐", "sectionTitle")
        self.empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(self.empty_title)
        self.empty_hint = text("App 会自动整理推荐并创建日期文件夹。", "muted")
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(self.empty_hint)
        empty_layout.addStretch()
        layout.addWidget(self.empty, 1)
        footer = QHBoxLayout()
        saved = text("推荐和作品按日期自动保存", "muted")
        saved.setWordWrap(False)
        footer.addWidget(saved)
        footer.addStretch()
        folder = QPushButton("打开当天文件夹")
        folder.clicked.connect(self.folderRequested)
        footer.addWidget(folder)
        self.details = QPushButton("查看执行记录")
        self.details.clicked.connect(self.detailsRequested)
        footer.addWidget(self.details)
        layout.addLayout(footer)

    def set_data(self, value, days, *, busy=False):
        self.day = value["day"]
        self.date_picker.blockSignals(True)
        self.date_picker.clear()
        for day in days:
            self.date_picker.addItem(day["day"] + " · 推荐", day["day"])
        self.date_picker.setCurrentIndex(self.date_picker.findData(self.day))
        self.date_picker.blockSignals(False)
        brief = value["brief"]
        self.scope.setText(f"{brief['domain']}  ·  {brief['audience']}  ·  {brief['aspect']}")
        key = (self.day, value["artifact_id"])
        candidates = value["candidates"]
        if self.render_key != key:
            while self.cards.count():
                item = self.cards.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            for index, candidate in enumerate(candidates):
                card = QFrame()
                card.setObjectName("recommendationCard")
                content = QVBoxLayout(card)
                content.setContentsMargins(22, 24, 22, 22)
                content.setSpacing(14)
                content.addWidget(text("推荐选题", "eyebrow"))
                content.addWidget(text(candidate["title"], "recommendationTitle"))
                content.addWidget(text(candidate["angle"], "recommendationAngle"))
                if candidate.get("reason"):
                    content.addWidget(text("为什么值得讲", "recommendationSection"))
                    content.addWidget(text(candidate["reason"], "muted"))
                if candidate.get("outline"):
                    content.addWidget(text("可以讲什么", "recommendationSection"))
                    content.addWidget(text("\n\n".join("• " + point for point in candidate["outline"]), "muted"))
                content.addStretch()
                seconds = candidate["suggested_duration_ms"] / 1000
                content.addWidget(text(f"建议 {seconds:g} 秒", "muted"))
                choose = QPushButton("用这个选题")
                choose.setObjectName("primary")
                choose.setProperty("candidateId", candidate["candidate_id"])
                choose.clicked.connect(lambda checked=False, d=self.day, c=candidate["candidate_id"], a=value["artifact_id"]: self.chosen.emit(d, c, a))
                content.addWidget(choose)
                self.cards.addWidget(card, index // 3, index % 3)
            for col in range(3):
                self.cards.setColumnStretch(col, 1)
            self.render_key = key
        self.scroll.setVisible(bool(candidates))
        self.empty.setVisible(not candidates)
        step = value["step"]
        running = busy or step["state"] == "RUNNING"
        self.progress.setVisible(running)
        self.generate.setEnabled(not running and step["state"] != "UNKNOWN")
        self.preferences.setEnabled(not running)
        self.generate.setText("正在生成…" if running else "换一批推荐" if candidates else "生成推荐")
        for choose in self.scroll.findChildren(QPushButton):
            choose.setEnabled(not running)
        if running:
            self.status.setText("正在为你整理推荐选题，你可以继续查看已有作品。")
            self.empty_title.setText("正在生成今天的推荐")
            self.empty_hint.setText("无需创建项目或文件夹，生成后直接选择你想做的内容。")
        elif candidates and not step["error_code"]:
            self.status.setText("选一个你想讲的题目，文稿准备交给 App。")
        else:
            messages = {"AUTH_REQUIRED": "请先登录 Antigravity CLI，再点击生成推荐。",
                        "CLI_NOT_FOUND": "请在设置中配置 Antigravity，再生成推荐。",
                        "QUOTA_EXHAUSTED": "推荐服务额度已用完，已有内容仍然保留。",
                        "RECOVERY_REQUIRED": "上次推荐结果尚未确认，请先在执行记录中核对。"}
            self.status.setText(messages.get(step["error_code"], "推荐尚未完成，可查看执行记录后继续。" if step["error_code"] else "今天还没有推荐，点击生成即可。"))
            if candidates:
                self.status.setText("本次更新未完成，仍可使用上次推荐；详情见执行记录。")
            self.empty_title.setText("推荐暂未就绪" if step["error_code"] else "今天，想讲点什么？")
            self.empty_hint.setText("点击上方「生成推荐」，App 会按你的内容方向整理选题。")
