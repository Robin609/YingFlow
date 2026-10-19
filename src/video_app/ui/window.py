"""Integrated, usable desktop framework. No simulated inference responses."""
from __future__ import annotations

import difflib
import json
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import Qt, QThread, Signal, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QPixmap
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QComboBox, QStackedWidget, QFrame, QLineEdit, QTextEdit,
    QPlainTextEdit, QFormLayout, QSpinBox, QDoubleSpinBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QDialog, QDialogButtonBox, QMessageBox,
    QFileDialog, QSplitter, QProgressBar, QScrollArea, QCheckBox,
)

from ..config import load_settings
from ..core.engine import Engine
from ..core.daily import DailyRecommendations, today
from ..core.production import VideoProduction
from ..core.workflow import STAGES, STAGE_MAP, PROVIDERS, STATE_NAMES, WorkflowError, dependencies
from .recommendations import RecommendationsPage, RecommendationPreferences
from .connection_wizard import BrowserConnectionWizard
from .video_options import VideoOptionsDialog, speech_description
from .theme import STYLESHEET, load_system_fonts


def label(text, name=None):
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(True)
    return widget


def button(text, callback, primary=False, name=None):
    widget = QPushButton(text)
    widget.setObjectName(name or ("primary" if primary else ""))
    if primary and name:
        widget.setStyleSheet("background:#edb46a;color:#191b20;font-weight:700;")
    widget.clicked.connect(callback)
    return widget


def row(*widgets, stretch=False):
    layout = QHBoxLayout()
    layout.setSpacing(10)
    for widget in widgets:
        layout.addWidget(widget)
    if stretch:
        layout.addStretch()
    return layout


def table(headers, editable=False):
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.setAlternatingRowColors(True)
    widget.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    widget.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
    if not editable:
        widget.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    widget.verticalHeader().hide()
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    widget.verticalHeader().setDefaultSectionSize(46)
    return widget


def put_table(widget, rows):
    widget.setRowCount(len(rows))
    for r, values in enumerate(rows):
        for c, value in enumerate(values):
            widget.setItem(r, c, QTableWidgetItem(str(value)))


class ProjectDialog(QDialog):
    def __init__(self, parent=None, project=None):
        super().__init__(parent)
        self.setWindowTitle("项目设置" if project else "新建项目")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 24)
        layout.addWidget(label("给这次创作一个清晰的起点", "sectionTitle"))
        layout.addWidget(label("先设定要求，再在同一个工作台内推进每个阶段。", "muted"))
        form = QFormLayout()
        form.setSpacing(14)
        settings = project["settings"] if project else {}
        self.name = QLineEdit(project["name"] if project else "")
        self.name.setObjectName("projectName")
        self.name.setPlaceholderText("例如：ADC 过采样与抖动技术")
        self.domain = QLineEdit(settings.get("domain", "嵌入式技术原理"))
        self.audience = QLineEdit(settings.get("audience", "嵌入式开发者与电子爱好者"))
        self.language = QComboBox()
        self.language.setEditable(True)
        self.language.addItems(["中文", "English"])
        self.language.setCurrentText(settings.get("language", "中文"))
        self.duration = QDoubleSpinBox()
        self.duration.setRange(1, 3600)
        self.duration.setDecimals(3)
        self.duration.setSuffix(" 秒")
        self.duration.setValue(settings.get("target_ms", 230000) / 1000)
        self.aspect = QComboBox()
        self.aspect.addItems(["4:3", "16:9", "9:16", "1:1"])
        self.aspect.setCurrentText(settings.get("aspect", "4:3"))
        self.kind = QComboBox()
        self.kind.addItem("文案与分镜", "text_pipeline")
        self.kind.addItem("技术讲解视频（本地动画与配音）", "full_video")
        if project:
            self.kind.setCurrentIndex(self.kind.findData(project["job"]["kind"]))
            self.kind.setEnabled(False)
        for title, field in [("项目名称", self.name), ("内容领域", self.domain), ("目标受众", self.audience),
                             ("语言", self.language), ("目标时长", self.duration), ("画幅", self.aspect), ("任务范围", self.kind)]:
            form.addRow(title, field)
        layout.addLayout(form)
        layout.addWidget(label("默认参考：约 230 秒、4:3 的技术讲解；所有参数均可调整。", "muted"))
        actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        actions.button(QDialogButtonBox.StandardButton.Save).setText("保存项目" if project else "创建项目")
        actions.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        actions.accepted.connect(self.accept)
        actions.rejected.connect(self.reject)
        layout.addWidget(actions)

    def values(self):
        return self.name.text(), {"domain": self.domain.text(), "audience": self.audience.text(),
                                  "language": self.language.currentText(), "aspect": self.aspect.currentText(),
                                  "target_ms": round(self.duration.value() * 1000)}, self.kind.currentData()


class ShotDialog(QDialog):
    def __init__(self, shot, parent=None):
        super().__init__(parent)
        self.setObjectName("shotEditor")
        self.setWindowTitle("编辑镜头 · " + shot["shot_id"])
        self.resize(800, 660)
        layout = QVBoxLayout(self)
        layout.addWidget(label(shot["shot_id"], "sectionTitle"))
        form = QFormLayout()
        self.duration = QDoubleSpinBox()
        self.duration.setRange(0.001, 3600)
        self.duration.setDecimals(3)
        self.duration.setSuffix(" 秒")
        self.duration.setValue(shot["duration_ms"] / 1000)
        self.kind = QComboBox()
        self.kind.addItems(["技术动画", "生成视频", "导入素材"])
        if shot.get("visual_type") and self.kind.findText(shot["visual_type"]) < 0:
            self.kind.addItem(shot["visual_type"])
        self.kind.setCurrentText(shot.get("visual_type", "技术动画"))
        form.addRow("计划时长", self.duration)
        form.addRow("画面类型", self.kind)
        layout.addLayout(form)
        layout.addWidget(label("对应旁白片段", "muted"))
        self.narration = QPlainTextEdit(shot.get("narration", ""))
        self.narration.setObjectName("shotNarration")
        layout.addWidget(self.narration, 1)
        layout.addWidget(label("画面方案", "muted"))
        self.prompt = QPlainTextEdit(shot.get("visual_prompt", ""))
        self.prompt.setObjectName("shotVisualPrompt")
        layout.addWidget(self.prompt, 2)
        self.asset = QLineEdit(shot.get("asset_path", ""))
        self.asset.setPlaceholderText("可选：用于预览的本地素材路径")
        layout.addLayout(row(self.asset, button("选择素材…", self._browse)))
        layout.addWidget(label("要让本地画面进入成片，请在“素材”页导入并点击“用于此镜头”。此处仍可填写路径进行预览。", "muted"))
        actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        actions.button(QDialogButtonBox.StandardButton.Save).setText("应用到分镜")
        actions.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        actions.accepted.connect(self.accept)
        actions.rejected.connect(self.reject)
        layout.addWidget(actions)

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择本地素材", "", "媒体文件 (*.mp4 *.mov *.webm *.mkv *.wav *.mp3 *.m4a);;所有文件 (*)")
        if path:
            self.asset.setText(path)


class PreflightWorker(QThread):
    completed = Signal(dict)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings

    def run(self):
        from ..__main__ import preflight
        try:
            self.completed.emit(preflight(self.settings))
        except Exception as error:
            self.completed.emit({"error": str(error)})


class Window(QMainWindow):
    PAGES = ("我的作品", "创作进度", "选题", "文稿", "分镜", "素材", "运行记录", "设置", "成片", "每日推荐")

    def __init__(self, engine: Engine, tasks=None, *, auto_recommendations=True):
        super().__init__()
        self.engine = engine
        self.tasks = tasks
        self.auto_recommendations = auto_recommendations
        self.daily = DailyRecommendations(engine)
        self.daily_requested = set()
        self.calendar_day = today()
        self.auto_projects = set()
        self.continue_when_idle = set()
        self.live_buffers = {}
        self.closing = False
        self.project_id = None
        self.doc_loaded_id = ""
        self.shots_loaded_id = ""
        self.dirty = set()
        self.loading = False
        self.worker = None
        self.browser_wizard = None
        self.video_updates = {}
        self.setWindowTitle("Video Workbench · 视频制作工作台")
        self.resize(1440, 920)
        self.setMinimumSize(1120, 760)
        self.setStyleSheet(STYLESHEET)
        shell = QWidget()
        self.setCentralWidget(shell)
        outer = QHBoxLayout(shell)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._sidebar(outer)
        body = QWidget()
        main = QVBoxLayout(body)
        main.setContentsMargins(32, 24, 32, 20)
        main.setSpacing(16)
        self.breadcrumb = label("创作空间 / 项目", "eyebrow")
        main.addWidget(self.breadcrumb)
        self.title = label("你的创作项目", "pageTitle")
        self.subtitle = label("把选题、文稿、分镜与制作流程，放在同一个工作台。", "muted")
        header = QVBoxLayout()
        header.addWidget(self.title)
        header.addWidget(self.subtitle)
        main.addLayout(header)
        self.work_tabs = QWidget()
        tabs = QHBoxLayout(self.work_tabs)
        tabs.setContentsMargins(0, 0, 0, 0)
        self.work_tab_buttons = {}
        for index, title in ((3, "文稿"), (4, "分镜"), (5, "素材"), (8, "成片")):
            control = button(title, lambda checked=False, i=index: self.navigate(i))
            control.setCheckable(True)
            tabs.addWidget(control)
            self.work_tab_buttons[index] = control
        tabs.addStretch()
        tabs.addWidget(button("创作进度", lambda: self.navigate(1)))
        tabs.addWidget(button("运行记录", lambda: self.navigate(6)))
        main.addWidget(self.work_tabs)
        self.stack = QStackedWidget()
        main.addWidget(self.stack, 1)
        self.notice = label("所有项目和版本保存在本机。", "statusLine")
        main.addWidget(self.notice)
        outer.addWidget(body, 1)
        self.builders = [self._projects_page, self._workflow_page, self._topic_page, self._document_page,
                         self._storyboard_page, self._production_page, self._tasks_page, self._connections_page, self._export_page,
                         self._daily_page]
        for build in self.builders:
            page = QWidget()
            page.setLayout(QVBoxLayout())
            page.layout().setContentsMargins(0, 0, 0, 0)
            page.layout().setSpacing(14)
            build(page.layout())
            self.stack.addWidget(page)
        self._refresh_projects()
        self.navigate(9)
        if self.tasks:
            self.tasks.event.connect(self._task_event)
            self.tasks.finished.connect(lambda: self.close() if self.closing else None)
        self.day_timer = QTimer(self)
        self.day_timer.setInterval(60000)
        self.day_timer.timeout.connect(self._check_day)
        self.day_timer.start()

    def _sidebar(self, outer):
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(204)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(16, 26, 16, 20)
        layout.setSpacing(9)
        layout.addWidget(label("VIDEO\nWORKBENCH", "brand"))
        layout.addWidget(label("视频制作工作台", "muted"))
        layout.addSpacing(24)
        self.project_picker_panel = QWidget()
        picker_layout = QVBoxLayout(self.project_picker_panel)
        picker_layout.setContentsMargins(0, 0, 0, 0)
        picker_layout.addWidget(label("当前创作", "eyebrow"))
        self.project_picker = QComboBox()
        self.project_picker.setObjectName("projectPicker")
        self.project_picker.setMinimumHeight(38)
        self.project_picker.currentIndexChanged.connect(self._pick_project)
        picker_layout.addWidget(self.project_picker)
        layout.addWidget(self.project_picker_panel)
        layout.addSpacing(14)
        self.nav = {}
        for i, name in ((9, "每日推荐"), (0, "我的作品"), (3, "创作工作台"), (7, "设置")):
            control = button(name, lambda checked=False, index=i: self.navigate(index))
            control.setObjectName("nav")
            control.setCheckable(True)
            self.nav[i] = control
            layout.addWidget(control)
        layout.addStretch()
        layout.addWidget(label("DEVELOPMENT  /  0.3", "eyebrow"))
        layout.addWidget(label("本地保存 · 版本可追溯", "muted"))
        data_button = button("打开数据目录", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.engine.root))))
        data_button.setToolTip(str(self.engine.root))
        layout.addWidget(data_button)
        outer.addWidget(sidebar)

    def _notify(self, text):
        self.notice.setText(text)

    def action(self, callback):
        try:
            return callback()
        except (WorkflowError, OSError, ValueError) as error:
            self._notify(f"{getattr(error, 'code', 'OPERATION_FAILED')} · {error}")
            return None

    def _mark_dirty(self, section):
        if not self.loading:
            self.dirty.add(section)

    def _can_discard(self):
        if not self.dirty:
            return True
        choice = QMessageBox.question(self, "有未保存的内容", "切换会丢弃当前未保存的编辑。继续吗？",
                                      QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                                      QMessageBox.StandardButton.Cancel)
        return choice == QMessageBox.StandardButton.Discard

    def navigate(self, index):
        if index not in {0, 7, 9} and not self.project_id:
            self._notify("从每日推荐选择一个选题，或打开已有作品。")
            index = 9
        previous = self.stack.currentIndex()
        if previous == index and self.dirty:
            return
        if previous != index and not self._can_discard():
            return
        if previous != index:
            self.dirty.clear()
        self.stack.setCurrentIndex(index)
        self.project_picker_panel.setVisible(index != 9)
        active = index if index in {0, 7, 9} else 3
        for i, control in self.nav.items():
            control.setChecked(i == active)
        self.work_tabs.setVisible(index in {1, 2, 3, 4, 5, 6, 8})
        for i, control in self.work_tab_buttons.items():
            control.setChecked(i == index)
        project_name = self.engine.project(self.project_id)["name"] if self.project_id else "创作空间"
        self.breadcrumb.setText(f"{project_name}  /  {self.PAGES[index]}")
        headings = [
            ("我的作品", "从每日推荐开始的创作会自动保存在这里。"),
            ("接着完成这次创作", "文稿准备交给 App，你只需确认内容与分镜。"),
            ("推荐选题", "选择推荐内容后，App 自动准备文稿。"),
            ("文稿工作台", "初稿、审核、终稿在同一处编辑。保存草稿不会替换已确认版本。"),
            ("让每一段旁白，都有对应画面", "稳定镜头 ID · 旁白片段 · 画面方案 · 毫秒时长"),
            ("管理画面与声音", "把素材归到对应镜头，在工作台内预览，跟踪输入版本变化。"),
            ("任务与执行记录", "查看每次尝试的真实来源、阶段和状态。"),
            ("设置", "管理创作工具的连接与登录。"),
            ("把这次创作，变成视频", "技术动画／导入画面、所选配音与同步字幕，生成 MP4 后直接预览。"),
            ("每日推荐", "内容方向交给你，值得讲的选题交给 App。"),
        ]
        self.title.setText(headings[index][0])
        self.subtitle.setText(headings[index][1])
        if index == 0:
            self._load_projects_table()
        elif index == 1:
            self._load_workflow()
        elif index == 2:
            self._load_topic()
        elif index == 3:
            self._load_document()
        elif index == 4:
            self._load_shots()
        elif index == 5:
            self._load_production()
        elif index == 6:
            self._load_tasks()
        elif index == 7:
            self._load_connections()
        elif index == 8:
            self._load_export()
        elif index == 9:
            self._load_daily()

    def _refresh_projects(self):
        self.loading = True
        self.project_picker.blockSignals(True)
        self.project_picker.clear()
        self.project_picker.addItem("选择项目…", None)
        batches = {item["project_id"] for item in self.daily.days()}
        for project in self.engine.list_projects():
            if project["id"] in batches:
                continue
            self.project_picker.addItem(project["name"], project["id"])
        self.project_picker.setCurrentIndex(max(0, self.project_picker.findData(self.project_id)))
        self.project_picker.blockSignals(False)
        self.loading = False
        self._load_projects_table()

    def _pick_project(self):
        project_id = self.project_picker.currentData()
        if project_id == self.project_id or self.loading:
            return
        if not self._can_discard():
            self._refresh_projects()
            return
        self.dirty.clear()
        self.project_id = project_id
        self.navigate(1 if project_id else 0)

    def open_project(self, project_id):
        if not self._can_discard():
            return
        self.dirty.clear()
        self.project_id = project_id
        self._refresh_projects()
        self.navigate(1)
        self._notify("项目已打开；所有历史版本均保留在本机。")

    def _daily_page(self, layout):
        self.recommendations = RecommendationsPage()
        self.recommendations.requested.connect(self._request_daily)
        self.recommendations.chosen.connect(self._choose_recommendation)
        self.recommendations.daySelected.connect(self._select_recommendation_day)
        self.recommendations.preferencesRequested.connect(self._recommendation_preferences)
        self.recommendations.folderRequested.connect(self._open_daily_folder)
        self.recommendations.detailsRequested.connect(self._daily_details)
        layout.addWidget(self.recommendations, 1)

    def _load_daily(self):
        day = self.recommendations.day or today()
        value = self.daily.sync(day)
        p = value["project_id"]
        busy = bool(self.tasks and self.tasks.busy(p))
        self.recommendations.set_data(value, self.daily.days(), busy=busy)
        step = value["step"]
        # A daily first visit queues once. Failed/unknown previous requests need
        # an explicit action, not a refresh-driven automatic retry loop.
        attempted = any(a["stage"] == "topic" for a in self.engine.attempts(p))
        if (self.tasks and self.auto_recommendations and day == today() and not value["candidates"] and not attempted
                and step["state"] == "PENDING" and p not in self.daily_requested):
            self.daily_requested.add(p)
            QTimer.singleShot(0, lambda d=day: self._request_daily(d))

    def _request_daily(self, day=None):
        day = day or self.recommendations.day or today()
        value = self.daily.contents(day)
        p = value["project_id"]
        if not self.tasks:
            self._notify("当前窗口未启用后台服务，无法生成推荐。")
            return
        if value["step"]["state"] in {"UNKNOWN", "RUNNING"}:
            self._notify("推荐仍在执行或结果待核对，没有重复发送。")
            return
        self.daily_requested.add(p)
        if self.tasks.submit(p, retry=bool(value["candidates"])):
            if self.recommendations.day == day:
                self.recommendations.set_data(value, self.daily.days(), busy=True)
            self._notify("正在生成推荐；日期文件夹已自动创建。")

    def _choose_recommendation(self, day, candidate_id, artifact_id):
        if not self._can_discard():
            return
        p = self.action(lambda: self.daily.choose(day, candidate_id, expected_artifact_id=artifact_id))
        if p:
            self.dirty.clear()
            self.open_project(p)
            pending = next((s for s in self.engine.steps(p) if s["state"] not in {"SUCCEEDED", "SKIPPED"}), None)
            if pending is None:
                self.navigate(8)
                return
            if self.engine.project(p)["job"]["state"] == "CANCELLED":
                return
            self.open_stage(pending["stage"])
            if pending["stage"] != "draft" or pending["state"] != "PENDING":
                return
            self.auto_projects.add(p)
            if self.tasks:
                self._queue_pipeline(p)

    def _select_recommendation_day(self, day):
        if day:
            self.recommendations.day = day
            self.action(self._load_daily)

    def _recommendation_preferences(self):
        day = self.recommendations.day or today()
        batch = self.daily.ensure(day)
        p = batch["project_id"]
        dialog = RecommendationPreferences(self.engine.project(p)["settings"], self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            def save():
                brief = dialog.values()
                self.engine.update_project(p, day + " 每日推荐", brief)
                self.engine.save_settings({"recommendation_brief": brief})
                self._request_daily(day)
            self.action(save)

    def _open_daily_folder(self):
        batch = self.daily.ensure(self.recommendations.day or today())
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.engine.root / batch["relative_path"])))

    def _daily_details(self):
        batch = self.daily.ensure(self.recommendations.day or today())
        self.open_project(batch["project_id"])
        self.navigate(6)

    def _check_day(self):
        day = today()
        if day != self.calendar_day:
            old = self.calendar_day
            self.calendar_day = day
            self.daily.ensure(day)
            if self.recommendations.day == old:
                self.recommendations.day = day
            self._load_daily()
            if self.tasks and self.auto_recommendations and self.recommendations.day != day:
                self._request_daily(day)

    def _projects_page(self, layout):
        self.show_archived = QCheckBox("显示已归档项目")
        self.show_archived.toggled.connect(self._load_projects_table)
        layout.addLayout(row(button("从每日推荐开始", lambda: self.navigate(9), True),
                             button("自己写一个选题", self.new_project, name="newProject"),
                             button("打开选中作品", self._open_selected), button("归档 / 恢复", self._archive_selected),
                             self.show_archived, stretch=True))
        self.project_table = table(["项目", "内容方向", "规格", "任务状态", "更新时间"])
        self.project_table.setObjectName("projectTable")
        self.project_table.cellDoubleClicked.connect(lambda *_: self._open_selected())
        layout.addWidget(self.project_table, 1)
        layout.addWidget(label("每天的推荐与作品自动归入日期文件夹，无需手动创建目录。", "muted"))

    def _load_projects_table(self):
        if not hasattr(self, "project_table"):
            return
        batches = {item["project_id"] for item in self.daily.days()}
        self.project_rows = [p for p in self.engine.list_projects(self.show_archived.isChecked()) if p["id"] not in batches]
        rows = []
        for project in self.project_rows:
            config = json.loads(project["settings_json"])
            video = VideoProduction(self.engine).latest(project["id"])
            state = STATE_NAMES.get(project["job_state"], project["job_state"])
            if video and video["state"] == "RUNNING":
                state = "视频制作中"
            elif video and video.get("result") and video["current"] and Path(video["result"]["video_file"]).is_file():
                state = "视频已生成"
            elif project["job_state"] == "SUCCEEDED" and project["kind"] == "text_pipeline":
                state = "文稿已就绪"
            rows.append([project["name"], config["domain"], f"{config['aspect']} · {config['target_ms']/1000:g} 秒",
                         state,
                         project["updated_at"][:16].replace("T", " ") + " UTC"])
        put_table(self.project_table, rows)
        if not rows:
            self._notify("还没有作品，从每日推荐中选一个题目即可开始。")

    def new_project(self):
        dialog = ProjectDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            name, settings, kind = dialog.values()
            project_id = self.action(lambda: self.engine.create_project(name, settings, kind))
            if project_id:
                self.open_project(project_id)

    def edit_project(self):
        if not self.project_id:
            return
        dialog = ProjectDialog(self, self.engine.project(self.project_id))
        if dialog.exec() == QDialog.DialogCode.Accepted:
            name, settings, _ = dialog.values()
            def save():
                self.engine.update_project(self.project_id, name, settings)
                self._refresh_projects()
                self._load_workflow()
                self._notify("项目设置已保存；要求变化时，相关下游需重新确认。")
            self.action(save)

    def _open_selected(self):
        index = self.project_table.currentRow()
        if index < 0:
            self._notify("请先选择一个项目。")
            return
        project = self.project_rows[index]
        if project["archived"]:
            self._notify("请先恢复这个归档项目。")
            return
        self.open_project(project["id"])

    def _archive_selected(self):
        index = self.project_table.currentRow()
        if index < 0:
            self._notify("请先选择一个项目。")
            return
        project = self.project_rows[index]
        self.engine.archive_project(project["id"], not project["archived"])
        if project["id"] == self.project_id:
            self.project_id = None
        self._refresh_projects()
        self._notify("项目已恢复。" if project["archived"] else "项目已归档，文件和版本没有删除。")

    def _workflow_page(self, layout):
        self.flow_summary = label("", "hint")
        layout.addWidget(self.flow_summary)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)
        self.cancel_button = button("取消任务", self._cancel)
        self.resume_button = button("恢复任务", self._resume)
        layout.addLayout(row(button("继续创作", self._advance, True, "advanceWorkflow"),
                             button("内容设置", self.edit_project), self.cancel_button, self.resume_button, stretch=True))
        layout.addWidget(label("文稿准备  →  分镜确认  →  生成视频", "sectionTitle"))
        self.current_work_hint = label("", "muted")
        layout.addWidget(self.current_work_hint)
        self.use_gpt = QCheckBox("使用 GPT 再润色一遍")
        self.use_gpt.toggled.connect(lambda checked: self.stage_cards["final"][2].setCurrentIndex(
            self.stage_cards["final"][2].findData("codex" if checked else "local")))
        layout.addWidget(self.use_gpt)
        details = button("高级配置", lambda: None)
        details.setCheckable(True)
        layout.addLayout(row(details, stretch=True))
        scroll = QScrollArea()
        self.workflow_details = scroll
        scroll.hide()
        details.toggled.connect(scroll.setVisible)
        scroll.setWidgetResizable(True)
        inner = QWidget()
        grid = QGridLayout(inner)
        grid.setContentsMargins(0, 2, 8, 2)
        grid.setSpacing(12)
        self.stage_cards = {}
        for index, stage in enumerate(STAGES):
            card = QFrame()
            card.setObjectName("card")
            card.setMinimumHeight(163)
            content = QVBoxLayout(card)
            content.setContentsMargins(16, 12, 16, 12)
            heading = label(stage.title, "nodeTitle")
            state_label = label("", "muted")
            content.addLayout(row(heading, state_label))
            content.addWidget(label(stage.description, "muted"))
            provider = QComboBox()
            for key, title in PROVIDERS.items():
                provider.addItem(title, key)
            provider.setObjectName(f"provider_{stage.key}")
            provider.currentIndexChanged.connect(lambda _, key=stage.key, combo=provider: self._provider_changed(key, combo))
            go = button("打开阶段", lambda checked=False, key=stage.key: self.open_stage(key))
            content.addLayout(row(provider, go))
            dependency_label = label("", "muted")
            content.addWidget(dependency_label)
            self.stage_cards[stage.key] = (card, state_label, provider, go, dependency_label)
            grid.addWidget(card, index // 3, index % 3)
        scroll.setWidget(inner)
        layout.addWidget(scroll, 1)
        layout.addStretch()

    def _load_workflow(self):
        if not self.project_id:
            return
        project = self.engine.project(self.project_id)
        steps = self.engine.steps(self.project_id)
        states = {s["stage"]: s["state"] for s in steps}
        completed = sum(states[s] == "SUCCEEDED" for s in ("review", "storyboard", "export"))
        settings = project["settings"]
        self.flow_summary.setText(f"{project['name']}  ·  {settings['aspect']}  ·  {settings['target_ms']/1000:g} 秒  ·  "
                                  f"{'文案与分镜' if project['job']['kind'] == 'text_pipeline' else '完整视频'}  /  "
                                  f"{STATE_NAMES[project['job']['state']]}")
        self.progress.setRange(0, 3)
        self.progress.setValue(completed)
        pending = next((s for s in steps if s["state"] not in {"SUCCEEDED", "SKIPPED"}), None)
        self.current_work_hint.setText(f"当前：{STAGE_MAP[pending['stage']].title} · {STATE_NAMES[pending['state']]}" if pending else "作品已保存，可在导出页打开。")
        self.use_gpt.blockSignals(True)
        self.use_gpt.setChecked(self.engine.step(self.project_id, "final")["provider"] == "codex")
        self.use_gpt.blockSignals(False)
        self.cancel_button.setEnabled(project["job"]["state"] not in {"SUCCEEDED", "CANCELLED"})
        self.resume_button.setEnabled(project["job"]["state"] == "CANCELLED")
        self.loading = True
        for step in steps:
            card, state_label, combo, go, dep_label = self.stage_cards[step["stage"]]
            state_label.setText(STATE_NAMES.get(step["state"], step["state"]))
            color = "#81d4bf" if step["state"] == "SUCCEEDED" else "#f0ba75" if step["state"] in {"BLOCKED", "WAITING_USER", "STALE"} else "#a1adbd"
            state_label.setStyleSheet(f"color:{color};font-weight:600;")
            combo.blockSignals(True)
            combo.setCurrentIndex(combo.findData(step["provider"]))
            combo.blockSignals(False)
            combo.setEnabled(step["stage"] not in {"brief", "export"} and step["state"] != "SKIPPED")
            deps = dependencies(step["stage"], project["job"]["kind"])
            dep_label.setText("输入：" + ("、".join(STAGE_MAP[key].title for key in deps) if deps else "项目要求"))
            if step["error_code"]:
                dep_label.setText(f"{step['error_code']} · 在任务页查看详情")
            if step["output_artifact_id"]:
                version = next(v for v in self.engine.versions(self.project_id, step["stage"]) if v["id"] == step["output_artifact_id"])
                dep_label.setText(dep_label.text() + "\n当前版本来源：" + PROVIDERS[version["output_provider"]])
            card.setToolTip(f"阶段 ID：{step['id']}\n活动执行：{step['active_run_id'] or '尚无'}")
        self.loading = False

    def _provider_changed(self, stage, combo):
        if self.loading or not self.project_id:
            return
        def change():
            self.engine.set_provider(self.project_id, stage, combo.currentData())
            if self.tasks:
                self.tasks.cancel(self.project_id)
            self._load_workflow()
            self._notify("节点提供方已保存。既有历史版本仍保留；来源变化后的结果需重新确认。")
        self.action(change)

    def open_stage(self, stage):
        if stage == "brief":
            self.edit_project()
        elif stage == "topic":
            self.navigate(2)
        elif stage in {"draft", "review", "final"}:
            if not self._can_discard():
                return
            self.dirty.clear()
            self.doc_stage.blockSignals(True)
            self.doc_stage.setCurrentIndex(self.doc_stage.findData(stage))
            self.doc_stage.blockSignals(False)
            self.navigate(3)
            self._load_document()
        elif stage == "storyboard":
            self.navigate(4)
        elif stage in {"visuals", "audio"}:
            self.media_kind.setCurrentIndex(self.media_kind.findData(stage))
            self.navigate(5)
        elif stage == "export":
            self.navigate(8)

    def _advance(self):
        if self.tasks:
            self.auto_projects.add(self.project_id)
            self._queue_pipeline(self.project_id)
            return
        def run():
            stage = self.engine.advance(self.project_id)
            self._load_workflow()
            if stage:
                current = self.engine.step(self.project_id, stage)
                if current["state"] == "BLOCKED":
                    self._notify(f"「{STAGE_MAP[stage].title}」的 {PROVIDERS[current['provider']]} 适配器尚未接入。可打开阶段手工编辑并确认。")
                else:
                    self.open_stage(stage)
            else:
                self._notify("文稿流程已完成，可到成片页生成或预览视频。")
        self.action(run)

    def _queue_pipeline(self, project_id, retry=False):
        if self.tasks.submit(project_id, retry=retry):
            self._notify("流程已提交后台，执行期间可以切换页面或项目。")
        else:
            self._notify("这个项目已在后台处理，没有重复提交。")

    def _continue_after_edit(self, project_id):
        if not self.tasks or project_id not in self.auto_projects:
            return
        if self.tasks.busy(project_id):
            self.tasks.cancel(project_id)
            self.continue_when_idle.add(project_id)
        else:
            self._queue_pipeline(project_id)

    def _task_event(self, event):
        project_id = event.get("project_id")
        kind = event.get("kind")
        if kind == "video":
            self.video_updates[project_id] = event
            if project_id == self.project_id:
                self._notify(event["message"])
                if self.stack.currentIndex() == 8:
                    self._load_export()
            return
        if kind == "browser":
            if self.browser_wizard:
                self.browser_wizard.handle_event(event)
            self._notify(event["message"])
            return
        batch = self.daily.batch(project_id) if project_id else None
        if batch:
            current = self.engine.step(project_id, "topic")
            if event.get("run_id") and event["run_id"] != current["active_run_id"]:
                return
            if kind in {"started", "completed", "stopped", "idle", "waiting", "error"}:
                value = self.daily.sync(batch["day"])
                if self.recommendations.day == batch["day"]:
                    self.recommendations.set_data(value, self.daily.days(), busy=self.tasks.busy(project_id))
                if self.stack.currentIndex() == 9:
                    self._notify("推荐已保存，选择喜欢的题目即可开始。" if value["candidates"] else self.recommendations.status.text())
            if self.project_id == project_id and self.stack.currentIndex() == 6:
                if kind in {"text", "preview"}:
                    self.live_output.setPlainText(event.get("text", "")[-4000:])
                else:
                    self._load_tasks()
            return
        if kind == "idle" and project_id in self.continue_when_idle:
            self.continue_when_idle.discard(project_id)
            self._queue_pipeline(project_id)
            return
        if project_id != self.project_id:
            if project_id is None and event.get("message"):
                self._notify(event["message"])
            return
        if event.get("run_id") and event.get("stage"):
            current = self.engine.step(project_id, event["stage"])
            if current["active_run_id"] != event["run_id"]:
                return
        if kind in {"text", "preview"}:
            run_id = event.get("run_id")
            value = event.get("text", "")
            buffer = value if kind == "preview" else self.live_buffers.get(run_id, "") + value
            self.live_buffers[run_id] = buffer[-4000:]
            self.live_output.setPlainText(buffer[-4000:])
        elif kind != "idle" and event.get("message"):
            self._notify(event["message"])
        page = self.stack.currentIndex()
        if page == 1:
            self._load_workflow()
        elif page == 6 and kind not in {"text", "preview"}:
            self._load_tasks()
        elif page == 8 and kind in {"idle", "error", "completed", "waiting"}:
            self._load_export()
        elif not self.dirty and kind in {"completed", "waiting", "idle"}:
            if page == 2:
                self._load_topic()
            elif page == 3:
                self._load_document()
            elif page == 4:
                self._load_shots()
        if kind == "waiting" and not self.dirty:
            self.open_stage(event["stage"])
        if kind == "completed" and event.get("stage") == "topic" and not self.dirty:
            self.navigate(2)
        if kind == "stopped" and event.get("stage") and self.tasks:
            step = self.engine.step(project_id, event["stage"])
            connection_errors = {"AUTH_REQUIRED", "UI_CHANGED", "MODEL_UNAVAILABLE", "MODEL_MISMATCH", "CLI_NOT_FOUND", "BROWSER_PORT_IN_USE",
                                 "BROWSER_OWNERSHIP_UNVERIFIED", "BROWSER_PROFILE", "BROWSER_PROFILE_UNAVAILABLE",
                                 "BROWSER_START_FAILED", "BROWSER_CONNECT_FAILED"}
            if step["provider"] == "gemini_web" and step["error_code"] in connection_errors and step["state"] != "UNKNOWN":
                self._show_browser_setup()

    def _cancel(self):
        if self.tasks:
            self.tasks.cancel(self.project_id)
            self.auto_projects.discard(self.project_id)
            self.continue_when_idle.discard(self.project_id)
        if self.action(lambda: self.engine.cancel(self.project_id)) is None and self.engine.project(self.project_id)["job"]["state"] != "CANCELLED":
            return
        self._load_workflow()
        self._notify("任务已取消；已有文稿与分镜版本保留。")

    def _resume(self):
        self.engine.resume(self.project_id)
        self._load_workflow()
        self._notify("任务已恢复。点击「继续流程」从有效阶段接着做。")

    def _topic_page(self, layout):
        layout.addWidget(label("运行流程取得候选后，选中一项再确认；也可直接编辑自己的题目。", "hint"))
        self.candidate_table = table(["候选标题", "切入角度", "建议时长"])
        self.candidate_table.setWordWrap(False)
        self.candidate_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.candidate_table.setMaximumHeight(190)
        self.candidate_table.itemSelectionChanged.connect(self._select_candidate)
        layout.addWidget(self.candidate_table)
        form = QFormLayout()
        self.topic_title = QLineEdit()
        self.topic_title.setObjectName("topicTitle")
        self.topic_title.setPlaceholderText("例如：给 ADC 加点噪声，为什么反而测得更细？")
        self.topic_angle = QTextEdit()
        self.topic_angle.setPlaceholderText("这条视频要解答什么问题？从哪个实验或现象切入？")
        self.topic_angle.setMinimumHeight(160)
        form.addRow("选题标题", self.topic_title)
        form.addRow("切入角度", self.topic_angle)
        layout.addLayout(form)
        layout.addLayout(row(button("保存草稿", lambda: self._save_topic(False)),
                             button("确认选题 → 文稿", lambda: self._save_topic(True), True, "confirmTopic"), stretch=True))
        layout.addStretch()
        self.topic_title.textChanged.connect(lambda: self._mark_dirty("topic"))
        self.topic_angle.textChanged.connect(lambda: self._mark_dirty("topic"))

    def _load_topic(self):
        self.loading = True
        payload = self.engine.latest(self.project_id, "topic") or {}
        versions = self.engine.versions(self.project_id, "topic")
        candidate_payload = payload
        if not payload.get("candidates"):
            for version in versions:
                value = self.engine.read_artifact(version["id"])
                if value.get("candidates"):
                    candidate_payload = value
                    break
        self.candidate_rows = candidate_payload.get("candidates", [])
        put_table(self.candidate_table, [[c["title"], c["angle"], f"{c['suggested_duration_ms']/1000:g} 秒"] for c in self.candidate_rows])
        self.topic_loaded_id = versions[0]["id"] if versions else ""
        self.topic_title.setText(payload.get("title", ""))
        self.topic_angle.setPlainText(payload.get("angle", ""))
        self.loading = False

    def _select_candidate(self):
        index = self.candidate_table.currentRow()
        if not self.loading and 0 <= index < len(self.candidate_rows):
            candidate = self.candidate_rows[index]
            self.topic_title.setText(candidate["title"])
            self.topic_angle.setPlainText(candidate["angle"])

    def _save_topic(self, confirm):
        def save():
            payload = {"title": self.topic_title.text(), "angle": self.topic_angle.toPlainText()}
            selected = self.candidate_table.currentRow()
            if 0 <= selected < len(self.candidate_rows):
                payload["candidate_id"] = self.candidate_rows[selected]["candidate_id"]
            self.engine.save_stage(self.project_id, "topic", payload,
                                   confirm=confirm, expected_latest=self.topic_loaded_id)
            self.dirty.discard("topic")
            self._load_topic()
            self._notify("选题已确认。" if confirm else "选题草稿已保存。")
            if confirm:
                self.open_stage("draft")
                self._continue_after_edit(self.project_id)
        self.action(save)

    def _document_page(self, layout):
        self.doc_stage = QComboBox()
        self.doc_stage.setObjectName("documentStage")
        for stage in ("draft", "review", "final"):
            self.doc_stage.addItem(STAGE_MAP[stage].title, stage)
        self.doc_stage.currentIndexChanged.connect(self._change_document_stage)
        self.doc_versions = QComboBox()
        self.doc_versions.setMinimumWidth(210)
        self.doc_versions.currentIndexChanged.connect(self._select_document_version)
        layout.addLayout(row(self.doc_stage, self.doc_versions,
                             button("使用上阶段正文", self._copy_upstream),
                             button("与已确认版比较", self._show_diff), stretch=True))
        self.review_controls = QWidget()
        review_row = QHBoxLayout(self.review_controls)
        review_row.setContentsMargins(0, 0, 0, 0)
        self.verdict = QComboBox()
        self.verdict.addItem("审核通过", "approve")
        self.verdict.addItem("仍需修订", "revise")
        self.verdict.addItem("不采用", "reject")
        self.review_notes = QTextEdit()
        self.review_notes.setMaximumHeight(86)
        self.review_notes.setPlaceholderText("审核问题与修改说明（本地人工审核）")
        review_row.addWidget(self.verdict)
        review_row.addWidget(self.review_notes, 1)
        layout.addWidget(self.review_controls)
        self.document_title = QLineEdit()
        self.document_title.setPlaceholderText("文稿标题")
        layout.addWidget(self.document_title)
        self.editor = QPlainTextEdit()
        self.editor.setObjectName("scriptEditor")
        self.editor.setPlaceholderText("在这里撰写或粘贴完整旁白正文。保存为不可变版本；确认后才进入下游。")
        self.editor.setFont(QFont("Microsoft YaHei UI", 12))
        layout.addWidget(self.editor, 1)
        self.doc_count = label("0 字符", "muted")
        self.editor.textChanged.connect(self._document_changed)
        self.document_title.textChanged.connect(lambda: self._mark_dirty("document"))
        self.verdict.currentIndexChanged.connect(lambda: self._mark_dirty("document"))
        self.review_notes.textChanged.connect(lambda: self._mark_dirty("document"))
        layout.addLayout(row(self.doc_count, button("保存新版本", lambda: self._save_document(False), name="saveDocument"),
                             button("确认本阶段", lambda: self._save_document(True), True, "confirmDocument")))
        layout.addWidget(label("保存版本保留历史。确认后更新下游输入；改动上游时，相关阶段需要重新确认。", "muted"))

    def _change_document_stage(self):
        if self.loading or not self.project_id:
            return
        old = getattr(self, "loaded_doc_stage", "draft")
        if not self._can_discard():
            self.doc_stage.blockSignals(True)
            self.doc_stage.setCurrentIndex(self.doc_stage.findData(old))
            self.doc_stage.blockSignals(False)
            return
        self.dirty.discard("document")
        self._load_document()

    def _document_changed(self):
        self.doc_count.setText(f"{len(self.editor.toPlainText())} 字符（含标点）")
        self._mark_dirty("document")

    def _load_document(self):
        self.loading = True
        stage = self.doc_stage.currentData()
        self.loaded_doc_stage = stage
        versions = self.engine.versions(self.project_id, stage)
        self.doc_loaded_id = versions[0]["id"] if versions else ""
        self.doc_versions.blockSignals(True)
        self.doc_versions.clear()
        if not versions:
            self.doc_versions.addItem("尚无版本", None)
        for index, version in enumerate(versions):
            self.doc_versions.addItem(f"v{len(versions)-index} · {'已确认' if version['confirmed'] else '草稿'} · {version['created_at'][11:19]} UTC", version["id"])
        self.doc_versions.blockSignals(False)
        payload = self.engine.read_artifact(versions[0]["id"]) if versions else {}
        self._fill_document(payload)
        self.loading = False

    def _fill_document(self, payload):
        self.document_payload = dict(payload)
        self.document_title.setText(payload.get("title", ""))
        self.editor.setPlainText(payload.get("script", ""))
        self.review_controls.setVisible(self.doc_stage.currentData() == "review")
        self.verdict.setCurrentIndex(max(0, self.verdict.findData(payload.get("verdict", "approve"))))
        self.review_notes.setPlainText(payload.get("notes", ""))

    def _select_document_version(self):
        if self.loading or not self.project_id or not self.doc_versions.currentData():
            return
        if not self._can_discard():
            self.doc_versions.blockSignals(True)
            self.doc_versions.setCurrentIndex(0)
            self.doc_versions.blockSignals(False)
            return
        self.loading = True
        self._fill_document(self.engine.read_artifact(self.doc_versions.currentData()))
        self.loading = False
        self.dirty.discard("document")
        self._notify("已读取历史版本。编辑后保存会创建新版本，不覆盖历史。")

    def _copy_upstream(self):
        stage = self.doc_stage.currentData()
        upstream = {"draft": "topic", "review": "draft", "final": "review"}[stage]
        payload = self.action(lambda: self.engine.latest(self.project_id, upstream, published=True))
        if payload is None:
            self._notify("上阶段尚无已确认内容。")
            return
        if self.editor.toPlainText().strip() and not self._can_discard():
            return
        self.document_title.setText(payload.get("title", ""))
        self.editor.setPlainText(payload.get("script", ""))
        if payload.get("shots"):
            self.document_payload["shots"] = payload["shots"]
        self._notify("已载入上阶段内容；编辑后保存即可形成当前阶段版本。")

    def _save_document(self, confirm):
        def save():
            stage = self.doc_stage.currentData()
            payload = {"title": self.document_title.text(), "script": self.editor.toPlainText()}
            if self.document_payload.get("shots"):
                payload["shots"] = self.document_payload["shots"]
            if stage == "review":
                payload.update(verdict=self.verdict.currentData(), notes=self.review_notes.toPlainText())
            self.engine.save_stage(self.project_id, stage, payload, confirm=confirm, expected_latest=self.doc_loaded_id)
            self.dirty.discard("document")
            self._load_document()
            self._notify(f"{STAGE_MAP[stage].title}{'已确认；下游需使用此版本继续。' if confirm else '新版本已保存，尚未发布到下游。'}")
            if confirm:
                self._continue_after_edit(self.project_id)
        self.action(save)

    def _show_diff(self):
        stage = self.doc_stage.currentData()
        old = self.action(lambda: self.engine.latest(self.project_id, stage, published=True)) or {}
        diff = "\n".join(difflib.unified_diff(old.get("script", "").splitlines(), self.editor.toPlainText().splitlines(),
                                              fromfile="已确认版本", tofile="当前编辑", lineterm=""))
        dialog = QDialog(self)
        dialog.setWindowTitle("版本差异")
        dialog.resize(820, 560)
        layout = QVBoxLayout(dialog)
        editor = QPlainTextEdit(diff or "当前正文与已确认版本相同。")
        editor.setReadOnly(True)
        layout.addWidget(editor)
        layout.addWidget(button("关闭", dialog.accept))
        dialog.exec()

    def _storyboard_page(self, layout):
        layout.addLayout(row(button("＋ 添加镜头", self._add_shot, name="addShot"),
                             button("编辑镜头", self._edit_shot, name="editShot"),
                             button("删除选中", self._delete_shot), button("上移", lambda: self._move_shot(-1)),
                             button("下移", lambda: self._move_shot(1)), button("预览素材", self._preview_shot), stretch=True))
        self.shot_table = table(["镜头 ID", "时长（秒）", "旁白片段", "画面方案", "画面类型", "本地素材路径"], editable=True)
        self.shot_table.setObjectName("shotTable")
        self.shot_table.setEditTriggers(QTableWidget.EditTrigger.EditKeyPressed | QTableWidget.EditTrigger.AnyKeyPressed)
        self.shot_table.cellDoubleClicked.connect(self._edit_shot)
        self.shot_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.shot_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.shot_table.verticalHeader().setDefaultSectionSize(96)
        self.shot_table.itemChanged.connect(self._shots_changed)
        layout.addWidget(self.shot_table, 1)
        self.shot_duration = label("", "muted")
        layout.addLayout(row(self.shot_duration, button("保存分镜草稿", lambda: self._save_shots(False)),
                             button("确认分镜", lambda: self._save_shots(True), True, "confirmShots")))
        layout.addWidget(label("双击镜头打开完整编辑器；也可选中单元格按 F2 快速编辑。素材路径仅用于预览，不会自动上传。", "hint"))

    def _edit_shot(self, *_):
        index = self.shot_table.currentRow()
        if index < 0:
            self._notify("请先选择一个镜头。")
            return
        def edit():
            shot = self._collect_shots()["shots"][index]
            dialog = ShotDialog(shot, self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                values = [f"{dialog.duration.value():g}", dialog.narration.toPlainText(), dialog.prompt.toPlainText(),
                          dialog.kind.currentText(), dialog.asset.text()]
                self.loading = True
                for column, value in enumerate(values, 1):
                    self.shot_table.item(index, column).setText(value)
                self.loading = False
                self._shots_changed()
        self.action(edit)

    def _load_shots(self):
        self.loading = True
        versions = self.engine.versions(self.project_id, "storyboard")
        self.shots_loaded_id = versions[0]["id"] if versions else ""
        payload = self.engine.latest(self.project_id, "storyboard") or {"shots": []}
        self.shot_table.setRowCount(0)
        for shot in payload["shots"]:
            self._insert_shot(shot)
        self.loading = False
        self._update_shot_duration()

    def _insert_shot(self, shot):
        r = self.shot_table.rowCount()
        self.shot_table.insertRow(r)
        values = [shot["shot_id"], f"{shot['duration_ms']/1000:g}", shot.get("narration", ""),
                  shot.get("visual_prompt", ""), shot.get("visual_type", "技术动画"), shot.get("asset_path", "")]
        for c, value in enumerate(values):
            item = QTableWidgetItem(str(value))
            if c == 0:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.shot_table.setItem(r, c, item)

    def _add_shot(self):
        self.loading = True
        total = sum(float(self.shot_table.item(r, 1).text()) for r in range(self.shot_table.rowCount())
                    if self.shot_table.item(r, 1) and self.shot_table.item(r, 1).text().replace(".", "", 1).isdigit())
        remaining = max(1, self.engine.project(self.project_id)["settings"]["target_ms"] / 1000 - total)
        self._insert_shot({"shot_id": "shot-" + uuid4().hex[:10], "duration_ms": round(remaining * 1000)})
        self.loading = False
        self.shot_table.selectRow(self.shot_table.rowCount() - 1)
        self._shots_changed()

    def _delete_shot(self):
        r = self.shot_table.currentRow()
        if r >= 0:
            self.shot_table.removeRow(r)
            self._shots_changed()

    def _move_shot(self, delta):
        r = self.shot_table.currentRow()
        other = r + delta
        if r < 0 or not 0 <= other < self.shot_table.rowCount():
            return
        self.loading = True
        for c in range(self.shot_table.columnCount()):
            a = self.shot_table.takeItem(r, c)
            b = self.shot_table.takeItem(other, c)
            self.shot_table.setItem(r, c, b)
            self.shot_table.setItem(other, c, a)
        self.loading = False
        self.shot_table.selectRow(other)
        self._shots_changed()

    def _shots_changed(self, *_):
        if not self.loading:
            self._mark_dirty("shots")
            self._update_shot_duration()

    def _collect_shots(self):
        shots = []
        for r in range(self.shot_table.rowCount()):
            values = [self.shot_table.item(r, c).text() if self.shot_table.item(r, c) else "" for c in range(6)]
            try:
                duration = round(float(values[1]) * 1000)
            except (ValueError, OverflowError) as error:
                raise WorkflowError("SHOT_DURATION", f"第 {r+1} 个镜头的时长不是有效数字。") from error
            shots.append({"shot_id": values[0], "order": r + 1, "duration_ms": duration,
                          "narration": values[2], "visual_prompt": values[3], "visual_type": values[4], "asset_path": values[5]})
        return {"shots": shots}

    def _update_shot_duration(self):
        if not self.project_id:
            return
        target = self.engine.project(self.project_id)["settings"]["target_ms"]
        try:
            shots = self._collect_shots()["shots"]
            total = sum(s["duration_ms"] for s in shots)
            self.shot_duration.setText(f"{len(shots)} 个镜头  ·  总时长 {total/1000:g} / {target/1000:g} 秒  ·  差额 {(total-target)/1000:+g} 秒")
        except WorkflowError:
            self.shot_duration.setText("请检查镜头时长")

    def _save_shots(self, confirm):
        def save():
            self.engine.save_stage(self.project_id, "storyboard", self._collect_shots(), confirm=confirm, expected_latest=self.shots_loaded_id)
            self.dirty.discard("shots")
            self._load_shots()
            self._notify("分镜已确认，可以到成片页生成视频。" if confirm else "分镜草稿已保存；确认前允许继续补全。")
            if confirm:
                self._continue_after_edit(self.project_id)
        self.action(save)

    def _preview_shot(self):
        r = self.shot_table.currentRow()
        if r < 0 or not self.shot_table.item(r, 5):
            self._notify("请先选择镜头并填写本地素材路径。")
            return
        path = Path(self.shot_table.item(r, 5).text())
        if not path.is_absolute():
            path = (self.engine.root / path).resolve()
            if not path.is_relative_to(self.engine.root):
                self._notify("素材相对路径不在当前数据目录中。")
                return
        if not path.is_file():
            self._notify("素材文件不存在。")
            return
        from .preview import PreviewDialog
        self.preview = PreviewDialog(path.resolve(), self)
        self.preview.show()

    def _production_page(self, layout):
        self.media_kind = QComboBox()
        self.media_kind.addItem("画面素材", "visuals")
        self.media_kind.addItem("配音素材", "audio")
        self.media_kind.currentIndexChanged.connect(lambda: self._load_production() if self.project_id else None)
        self.media_shot = QComboBox()
        self.media_shot.setMinimumWidth(240)
        self.apply_visual = button("用于此镜头", self._apply_visual, True, "applyVisualAsset")
        layout.addLayout(row(self.media_kind, self.media_shot,
                             button("导入本地素材", self._choose_media, name="importMedia"), self.apply_visual,
                             button("预览选中素材", self._preview_media, name="previewMedia"), stretch=True))
        self.media_table = table(["素材文件", "对应镜头", "状态", "来源", "导入时间"])
        self.media_table.setObjectName("mediaTable")
        self.media_table.cellDoubleClicked.connect(lambda *_: self._preview_media())
        self.media_table.itemSelectionChanged.connect(self._select_media_asset)
        layout.addWidget(self.media_table, 1)
        self.media_hint = label("", "hint")
        layout.addWidget(self.media_hint)

    def _load_production(self):
        if not self.project_id:
            return
        kind = self.media_kind.currentData()
        self.media_shot.setVisible(kind == "visuals")
        self.apply_visual.setVisible(kind == "visuals")
        selected = self.media_shot.currentData()
        self.media_shot.clear()
        payload = self.engine.latest(self.project_id, "storyboard", published=True) or {"shots": []}
        for shot in payload["shots"]:
            self.media_shot.addItem(f"{shot.get('order', 0):02d} · {shot['shot_id']} · {shot['duration_ms']/1000:g} 秒", shot["shot_id"])
        if self.media_shot.findData(selected) >= 0:
            self.media_shot.setCurrentIndex(self.media_shot.findData(selected))
        self.media_rows = self.engine.media(self.project_id, kind)
        bound = {s["shot_id"]: s.get("asset_path") for s in payload["shots"] if s.get("visual_type") == "导入素材"}
        def state(asset):
            if asset["state"] == "STALE":
                return "输入已变化 · 需重新确认"
            if kind == "visuals" and bound.get(asset["logical_shot_id"]) == asset["relative_path"]:
                return "当前镜头使用 · 已用于成片" if asset["state"] == "USED" else "当前镜头使用 · 待校验"
            return "已导入 · 待校验"
        put_table(self.media_table, [[a["original_name"], a["logical_shot_id"] or "整篇旁白",
                                     state(a), "本地导入",
                                     a["created_at"][:16].replace("T", " ") + " UTC"] for a in self.media_rows])
        self.media_hint.setText("选中素材后点击“用于此镜头”，App 会更新并确认分镜。图片或视频将与旁白、字幕合成；素材原声静音，短视频停留在最后一帧，长视频截取前段。原文件不变，原有分镜与成片保留。" if kind == "visuals" else
                                "配音与当前已确认终稿关联。导入与预览已可使用；真实时长校验、字幕对齐与最终合成将在媒体适配器接入后完成。")

    def _choose_media(self):
        kind = self.media_kind.currentData()
        file_filter = "画面素材 (*.mp4 *.mov *.webm *.mkv *.avi *.png *.jpg *.jpeg *.webp *.bmp);;所有文件 (*)" if kind == "visuals" else "音频素材 (*.wav *.mp3 *.aac *.m4a *.flac *.ogg);;所有文件 (*)"
        path, _ = QFileDialog.getOpenFileName(self, "导入本地素材", "", file_filter)
        if path:
            self.import_media_from(Path(path))

    def import_media_from(self, path):
        def import_asset():
            asset_id = self.engine.import_media(self.project_id, self.media_kind.currentData(), path, self.media_shot.currentData())
            self._load_production()
            index = next((i for i,a in enumerate(self.media_rows) if a["id"] == asset_id), -1)
            if index >= 0:
                self.media_table.selectRow(index)
            self._notify("素材已复制。点击“用于此镜头”后即可入片。" if self.media_kind.currentData() == "visuals" else "配音已导入，可先预览；成片当前仍使用本机配音。")
            return asset_id
        return self.action(import_asset)

    def _select_media_asset(self):
        index = self.media_table.currentRow()
        if self.media_kind.currentData() == "visuals" and 0 <= index < len(self.media_rows):
            shot = self.media_shot.findData(self.media_rows[index]["logical_shot_id"])
            if shot >= 0:
                self.media_shot.setCurrentIndex(shot)

    def _apply_visual(self):
        index = self.media_table.currentRow()
        if not 0 <= index < len(self.media_rows):
            self._notify("请先选择要使用的画面素材。")
            return
        asset = self.media_rows[index]
        allow_stale = False
        if asset["state"] == "STALE":
            choice = QMessageBox.question(self, "重新使用旧素材", "这个素材对应旧画面方案。仍要用于当前镜头吗？",
                                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if choice != QMessageBox.StandardButton.Yes:
                return
            allow_stale = True
        def apply():
            if self.dirty:
                raise WorkflowError("UNSAVED_CONTENT", "请先保存并确认当前编辑，再应用素材。")
            shot_id = self.engine.bind_visual_asset(self.project_id, asset["id"], allow_stale=allow_stale)
            self._load_production()
            self._notify(f"已用于镜头 {shot_id}，分镜已更新。到“成片”重新生成即可，旧成片仍保留。")
        self.action(apply)

    def _preview_media(self):
        index = self.media_table.currentRow()
        if index < 0:
            self._notify("请先选择一个素材。")
            return
        def preview():
            from .preview import PreviewDialog
            path = self.engine.media_path(self.media_rows[index]["id"])
            self.preview = PreviewDialog(path, self)
            self.preview.show()
        self.action(preview)

    def _tasks_page(self, layout):
        layout.addLayout(row(button("刷新记录", self._load_tasks), button("重试当前阶段", lambda: self._retry()),
                             button("核对未知结果", self._reconcile), button("确认重做未知请求", self._redo_unknown), stretch=True))
        splitter = QSplitter(Qt.Orientation.Vertical)
        self.attempt_table = table(["阶段", "尝试", "来源", "状态", "错误码", "执行 ID"])
        splitter.addWidget(self.attempt_table)
        self.event_log = QPlainTextEdit()
        self.event_log.setReadOnly(True)
        splitter.addWidget(self.event_log)
        splitter.setSizes([320, 220])
        layout.addWidget(splitter, 1)
        self.live_output = QPlainTextEdit()
        self.live_output.setReadOnly(True)
        self.live_output.setMaximumHeight(140)
        self.live_output.setPlaceholderText("提供方实时预览（通过校验前不会成为正式版本）")
        layout.addWidget(self.live_output)

    def _load_tasks(self):
        if not self.project_id:
            return
        attempts = self.engine.attempts(self.project_id)
        put_table(self.attempt_table, [[STAGE_MAP[a["stage"]].title, a["ordinal"], PROVIDERS[a["provider"]],
                                       STATE_NAMES.get(a["state"], a["state"]), a["error_code"] or "—", a["id"][:12]] for a in attempts])
        events = self.engine.events(self.project_id)
        self.event_log.setPlainText("\n\n".join(f"{e['created_at'][:19].replace('T', ' ')} UTC\n{e['message']}" for e in events))

    def _retry(self):
        if self.tasks:
            self.auto_projects.add(self.project_id)
            self._queue_pipeline(self.project_id, retry=True)
            return
        def retry():
            stage = self.engine.advance(self.project_id, retry=True)
            self._load_tasks()
            self._notify(f"已重新检查「{STAGE_MAP[stage].title}」，没有发出外部请求。" if stage else "没有需要重试的阶段。")
        self.action(retry)

    def _reconcile(self):
        if self.tasks:
            if self.tasks.submit(self.project_id, reconcile=True):
                self._notify("正在只读核对原结果，不会重新发送。")
        else:
            self._notify("当前离线窗口没有后台服务。")

    def _redo_unknown(self):
        if not self.tasks:
            return
        choice = QMessageBox.warning(self, "明确重做未知请求", "原请求可能已在外部完成。重做会发起新请求，可能再次消耗额度或费用。已核对原会话并确定仍要重做吗？",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if choice == QMessageBox.StandardButton.Yes:
            def redo():
                self.engine.authorize_redo_unknown(self.project_id)
                self.auto_projects.add(self.project_id)
                self._queue_pipeline(self.project_id)
            self.action(redo)

    def _connections_page(self, layout):
        layout.addWidget(label("首次使用或换电脑：用连接助手自动寻找 Chrome、配置专用窗口并完成 Gemini 登录。CLI 使用当前 Windows 用户的官方登录。", "hint"))
        self.browser_button = button("Gemini 连接助手", self._open_browser, True)
        self.browser_button.setEnabled(self.tasks is not None)
        self.speech_defaults = label("", "muted")
        connections_toolbar = row(self.browser_button, button("配音设置", self._speech_options, name="speechSettings"), self.speech_defaults)
        connections_toolbar.setStretch(2, 1)
        layout.addLayout(connections_toolbar)
        form = QFormLayout()
        form.setSpacing(12)
        self.connection_fields = {}
        for key, title in [("agy_executable", "Antigravity CLI"), ("codex_executable", "Codex CLI"),
                           ("chrome_executable", "专用 Chrome"), ("gemini_profile_dir", "专用浏览器目录"),
                           ("agy_model", "初稿模型"), ("codex_model", "GPT 模型"), ("gemini_mode", "Gemini 网页模式")]:
            field = QLineEdit()
            field.setPlaceholderText("自动发现；也可填写绝对路径" if key.endswith("executable") else "精确模型名称")
            if key == "gemini_profile_dir":
                field.setPlaceholderText("由连接助手自动创建；也可复用原 App 专用目录")
            if key == "gemini_mode":
                field.setPlaceholderText("网页菜单中的准确名称，例如 4 Argon")
                field.setToolTip("每次发送前自动选择并核对；找不到时暂停，不使用默认 Flash。")
            field.textChanged.connect(lambda _, section="connections": self._mark_dirty(section))
            self.connection_fields[key] = field
            if key.endswith("executable") or key == "gemini_profile_dir":
                holder = QWidget()
                callback = (lambda checked=False, f=field: self._browse_profile(f)) if key == "gemini_profile_dir" else (lambda checked=False, f=field: self._browse_executable(f))
                holder.setLayout(row(field, button("选择…", callback)))
                holder.layout().setContentsMargins(0, 0, 0, 0)
                form.addRow(title, holder)
            else:
                form.addRow(title, field)
        self.browser_port = QSpinBox()
        self.browser_port.setRange(1024, 65535)
        self.browser_port.setValue(9222)
        self.browser_port.valueChanged.connect(lambda _: self._mark_dirty("connections"))
        self.browser_auto_port = QCheckBox("自动避开已占用端口（推荐）")
        self.browser_auto_port.setChecked(True)
        self.browser_auto_port.toggled.connect(lambda checked: (self.browser_port.setEnabled(not checked), self._mark_dirty("connections")))
        self.browser_port.setEnabled(False)
        port_row = row(self.browser_auto_port, self.browser_port)
        form.addRow("浏览器本机端口", port_row)
        layout.addLayout(form)
        self.detect_button = button("检测本机入口", self._detect, True, "detectConnections")
        layout.addLayout(row(button("保存设置", self._save_connections), self.detect_button, stretch=True))
        self.connection_report = QPlainTextEdit()
        self.connection_report.setReadOnly(True)
        self.connection_report.setPlaceholderText("检测不会调用模型，不读取凭据，也不会修改账号登录。")
        layout.addWidget(self.connection_report, 1)
        layout.addWidget(label("浏览器登录保留在这台电脑的专用目录；换电脑后重新登录，不需要复制登录资料。", "muted"))

    def _open_browser(self):
        self._save_connections()
        if self.tasks:
            self._show_browser_setup()

    def _show_browser_setup(self):
        if not self.browser_wizard or (not self.browser_wizard.isVisible() and not self.browser_wizard.pending_id):
            if self.browser_wizard:
                self.browser_wizard.deleteLater()
            self.browser_wizard = BrowserConnectionWizard(self.engine, self.tasks, self)
            self.browser_wizard.accepted.connect(self._browser_setup_finished)
        self.browser_wizard.show()
        self.browser_wizard.raise_()
        self.browser_wizard.activateWindow()

    def _browser_setup_finished(self):
        if "connections" not in self.dirty:
            self._load_connections()
        self._notify("Gemini 连接已就绪。在创作进度中点击“继续创作”即可接着做，已确认文稿无需重做。")

    def _load_connections(self):
        self.loading = True
        values = load_settings(overrides=self.engine.get_settings())
        for key, field in self.connection_fields.items():
            field.setText(values.get(key) or "")
        self.browser_port.setValue(int(values.get("browser_port") or 9222))
        self.browser_auto_port.setChecked(bool(values.get("browser_auto_port", True)))
        self.browser_port.setEnabled(not self.browser_auto_port.isChecked())
        self.loading = False
        self._load_speech_options()

    def _browse_executable(self, field):
        path, _ = QFileDialog.getOpenFileName(self, "选择可执行文件", "", "Windows 程序 (*.exe);;所有文件 (*)")
        if path:
            field.setText(path)

    def _browse_profile(self, field):
        path = QFileDialog.getExistingDirectory(self, "选择 App 专用浏览器目录（不要选择日常 Chrome 目录）")
        if path:
            field.setText(path)

    def _save_connections(self):
        values = {key: field.text().strip() or None for key, field in self.connection_fields.items()}
        values["browser_port"] = self.browser_port.value()
        values["browser_auto_port"] = self.browser_auto_port.isChecked()
        self.engine.save_settings(values)
        self.dirty.discard("connections")
        self._notify("连接设置已保存在本机；未保存任何账号凭据。")

    def _detect(self):
        if self.worker and self.worker.isRunning():
            return
        self._save_connections()
        values = {key: field.text().strip() or None for key, field in self.connection_fields.items()}
        self.detect_button.setEnabled(False)
        self.detect_button.setText("正在检测…")
        self.worker = PreflightWorker(values, self)
        self.worker.completed.connect(self._detected)
        self.worker.start()

    def _detected(self, report):
        self.connection_report.setPlainText(json.dumps(report, ensure_ascii=False, indent=2))
        self.detect_button.setEnabled(True)
        self.detect_button.setText("检测本机入口")
        self._notify("入口检测完成。生成连通性尚未验收，未发起模型调用。")

    def _export_page(self, layout):
        self.video_summary = label("确认终稿与分镜后，即可生成视频。", "hint")
        layout.addWidget(self.video_summary)
        self.generate_video = button("生成视频", self._generate_video, True, "generateVideo")
        self.preview_video = button("预览视频", self._preview_video, name="previewVideo")
        self.video_folder_button = button("打开成片目录", self._open_video_folder)
        self.cancel_video = button("停止生成", self._cancel_video)
        layout.addLayout(row(self.generate_video, button("视频设置", self._video_options),
                             button("配音设置", self._speech_options, name="videoSpeechSettings"), self.preview_video,
                             self.video_folder_button, self.cancel_video, stretch=True))
        self.video_speech_defaults = label("", "muted")
        layout.addWidget(self.video_speech_defaults)
        self.video_progress = QProgressBar()
        self.video_progress.setRange(0, 100)
        self.video_progress.setTextVisible(False)
        layout.addWidget(self.video_progress)
        self.video_card = QFrame()
        self.video_card.setObjectName("card")
        card = QHBoxLayout(self.video_card)
        card.setContentsMargins(18, 18, 18, 18)
        self.video_cover = QLabel()
        self.video_cover.setFixedSize(320, 240)
        self.video_cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card.addWidget(self.video_cover)
        details = QVBoxLayout()
        self.video_details = label("", "sectionTitle")
        self.video_location = label("", "muted")
        self.video_location.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        details.addWidget(self.video_details)
        details.addWidget(self.video_location)
        card.addLayout(details, 1)
        layout.addWidget(self.video_card)
        layout.addWidget(label("按分镜混排技术动画和已应用的图片／视频。导入画面原声静音，较短时停留在末帧；声音在“配音设置”调整，清晰度在“视频设置”调整。", "muted"))
        layout.addStretch()
        layout.addWidget(label("文稿资料（可选）", "sectionTitle"))
        self.export_summary = label("", "hint")
        layout.addWidget(self.export_summary)
        layout.addWidget(label("script.md · 旁白文稿    /    storyboard.json · 分镜    /    project.json · 版本记录", "muted"))
        self.auto_export_path = label("", "muted")
        layout.addWidget(self.auto_export_path)
        layout.addLayout(row(button("导出文稿资料", self._choose_export, name="exportProject"),
                             button("资料另存到…", self._export_elsewhere), button("打开资料目录", self._open_export), stretch=True))
        self.export_path = QLineEdit()
        self.export_path.setReadOnly(True)
        self.export_path.setPlaceholderText("完成实际导出后，这里会显示结果位置")
        layout.addWidget(self.export_path)

    def _load_export(self):
        self._load_speech_options()
        steps = {s["stage"]: s for s in self.engine.steps(self.project_id)}
        project = self.engine.project(self.project_id)
        ready = all(steps[key]["state"] == "SUCCEEDED" for key in dependencies("export", project["job"]["kind"]))
        self.export_summary.setText("当前确认版本已就绪，可以导出。" if ready else "尚未就绪：请先确认终稿与分镜；上游变更后需重新确认下游。")
        previous = self.engine.latest(self.project_id, "export", published=True)
        self.export_path.setText(previous.get("folder", "") if previous else "")
        self.auto_export_path.setText("自动保存到：" + str(self.daily.work_folder(self.project_id)))
        text_ready = all(steps[key]["state"] == "SUCCEEDED" for key in ("final", "storyboard"))
        busy = bool(self.tasks and self.tasks.busy(self.project_id))
        video = VideoProduction(self.engine).latest(self.project_id)
        update = self.video_updates.get(self.project_id, {})
        result = (video or {}).get("result") or {}
        exists = bool(result.get("video_file") and Path(result["video_file"]).is_file())
        self.generate_video.setEnabled(text_ready and self.tasks is not None and not busy)
        self.generate_video.setText("重新生成视频" if exists else "生成视频")
        self.preview_video.setEnabled(exists)
        self.preview_video.setText("预览上个视频" if video and video.get("previous_result") else "预览视频")
        self.video_folder_button.setEnabled(bool(video and Path(video["folder"]).is_dir()))
        self.cancel_video.setVisible(bool(busy and video and video["state"] == "RUNNING"))
        self.video_progress.setVisible(bool(video))
        self.video_progress.setValue(update.get("percent", 100 if video and video["state"] in {"SUCCEEDED", "STALE"} else 0))
        if video and video["state"] == "RUNNING":
            self.video_summary.setText(update.get("message", "正在制作视频，可以切换页面，完成后会保存在作品目录。"))
        elif video and video["state"] in {"FAILED", "CANCELLED", "INTERRUPTED"}:
            self.video_summary.setText("上次生成未完成：" + (video["error"] or video["state"]) + "。可修改视频设置后重新生成。")
        elif exists:
            self.video_summary.setText("视频已生成，可以预览或打开成片目录。" if video["current"] else "已有视频对应旧版本。文稿、分镜或素材已变化，请重新生成；旧成片仍可预览。")
        else:
            self.video_summary.setText("文稿与分镜已就绪。点击“生成视频”，App 自动制作配音、字幕和 MP4。" if text_ready else "请先确认终稿与分镜，再生成视频。")
        self.video_card.setVisible(exists)
        if exists:
            cover = QPixmap(result.get("cover_file", ""))
            self.video_cover.setPixmap(cover.scaled(self.video_cover.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            quality = result["quality"]
            self.video_details.setText(f"MP4 成片 · {result['duration_ms']/1000:.1f} 秒\n{quality['width']} × {quality['height']} · {quality['fps']} fps · H.264 / AAC\n中文配音 + 画面字幕 + 独立 SRT")
            speech_source = {"google": "Google Cloud", "edge": "Edge 在线语音", "windows": "Windows 系统语音"}.get((result.get("speech") or {}).get("provider", "windows"), "所选语音")
            self.video_location.setText(result["video_file"] + f"\n配音：{speech_source} · {result['voice']}\n计划 {result['planned_ms']/1000:g} 秒；成片时长以实际配音为准。")

    def _load_speech_options(self):
        text = speech_description(self.engine.get_settings().get("video_options", {}))
        self.speech_defaults.setText("默认配音：" + text)
        self.video_speech_defaults.setText("下次生成使用：" + text)

    def _speech_options(self):
        self._video_options(speech_only=True)

    def _video_options(self, *, speech_only=False):
        dialog = VideoOptionsDialog(self.engine.get_settings().get("video_options", {}), self, speech_only=speech_only)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.engine.save_settings({"video_options": dialog.values()})
            self._load_speech_options()
            self._notify("默认配音已保存，后续生成时生效；已有视频不变。" if speech_only else
                         "视频设置已保存。已有成片保持不变，下一次生成时生效。")

    def _generate_video(self):
        def submit():
            if self.dirty:
                raise WorkflowError("UNSAVED_CONTENT", "请先保存并确认当前编辑，再生成视频。")
            VideoProduction(self.engine).inputs(self.project_id)
            VideoProduction(self.engine).visual_inputs(self.project_id)
            options = dict(self.engine.get_settings().get("video_options", {}))
            options.pop("google_confirmed", None)
            if options.get("speech_provider") == "google":
                choice = QMessageBox.question(self, "确认本次 Google Cloud 配音",
                                              "本次将发送已确认的分镜旁白到 Google Cloud 生成配音，可能消耗该账号额度或计费。\n"
                                              "声音：" + (options.get("google_voice") or "cmn-CN-Wavenet-B") +
                                              "\n是否确认发起本次调用？取消则不发送。",
                                              QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
                if choice != QMessageBox.StandardButton.Yes:
                    return
                options["google_confirmed"] = True
            if self.tasks.submit_video(self.project_id, options):
                self.video_updates[self.project_id] = {"percent": 0, "message": "视频任务已排队…"}
                self._notify("正在生成视频，无需再创建目录。")
                self._load_export()
            else:
                self._notify("这次创作已在后台处理，没有重复提交。")
        self.action(submit)

    def _preview_video(self):
        value = VideoProduction(self.engine).latest(self.project_id)
        path = Path(value["result"]["video_file"]) if value and value.get("result") else None
        if path and path.is_file():
            from .preview import PreviewDialog
            self.preview = PreviewDialog(path, self)
            self.preview.setWindowTitle("视频预览 · " + self.engine.project(self.project_id)["name"])
            self.preview.show()

    def _open_video_folder(self):
        value = VideoProduction(self.engine).latest(self.project_id)
        path = Path(value["result"]["video_file"]).parent if value and value.get("result") else Path(value["folder"]) if value else None
        if path and path.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _cancel_video(self):
        if self.tasks:
            self.tasks.cancel(self.project_id)
            self._notify("正在停止本地视频制作，原文稿与已有成片保留。")

    def _choose_export(self):
        self.export_to(self.daily.work_folder(self.project_id))

    def _export_elsewhere(self):
        destination = QFileDialog.getExistingDirectory(self, "选择导出位置")
        if destination:
            self.export_to(Path(destination))

    def export_to(self, destination):
        def export():
            folder = self.engine.export_project(self.project_id, destination)
            self._load_export()
            self._notify(f"已导出文稿、分镜与项目记录：{folder}")
            return folder
        return self.action(export)

    def _open_export(self):
        path = Path(self.export_path.text())
        if self.export_path.text() and path.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))
        else:
            self._notify("尚无可打开的导出目录。")

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self._notify("入口检测尚未结束，请稍后关闭窗口。")
            event.ignore()
            return
        if not self._can_discard():
            event.ignore()
            return
        if self.tasks and self.tasks.isRunning():
            if self.tasks.busy() and not self.closing:
                choice = QMessageBox.question(self, "后台任务尚未结束", "关闭将停止本地执行并保存现有结果。已提交的网页请求仍需核对。确定关闭吗？",
                                              QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                              QMessageBox.StandardButton.No)
                if choice != QMessageBox.StandardButton.Yes:
                    event.ignore()
                    return
            self.closing = True
            self.dirty.clear()
            self.tasks.shutdown()
            self._notify("正在停止后台并保存现场…")
            event.ignore()
            return
        self.engine.close()
        event.accept()


def run_app(data_root: Path, initial_settings=None, *, auto_recommendations=True):
    from PySide6.QtCore import QLockFile
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("Video Workbench")
    app.setOrganizationName("Robin Atlas")
    app.setStyle("Fusion")
    load_system_fonts()
    data_root = data_root.resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(data_root / "session.lock"))
    if not lock.tryLock(100):
        QMessageBox.information(None, "工作台已打开", "这个数据目录已有一个工作台窗口，请回到原窗口继续。")
        return 1
    try:
        engine = Engine(data_root)
        if initial_settings:
            allowed = {"agy_executable", "codex_executable", "chrome_executable", "agy_model", "codex_model", "gemini_mode", "browser_port", "browser_auto_port", "gemini_profile_dir"}
            engine.save_settings({key: value for key, value in initial_settings.items() if key in allowed})
        from .tasks import TaskService
        tasks = TaskService(data_root)
        window = Window(engine, tasks, auto_recommendations=auto_recommendations)
        tasks.start()
    except Exception as error:
        QMessageBox.critical(None, "无法打开工作台", f"无法打开项目数据目录：{data_root}\n\n{error}")
        lock.unlock()
        return 2
    window.show()
    result = app.exec()
    lock.unlock()
    return result
