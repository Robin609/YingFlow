"""Actual Qt/background integration using explicitly scripted offline providers."""
import asyncio
import json
import os
from pathlib import Path
import time
from uuid import uuid4

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from video_app.core.contracts import ProviderError, TextResult
from video_app.core.engine import Engine
from video_app.ui.tasks import TaskService
from video_app.ui.theme import load_system_fonts
from video_app.ui.window import Window

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "evidence" / ("async-ui-" + uuid4().hex[:10])
OUT.mkdir(parents=True)
checks = []


def check(condition, message):
    if not condition:
        raise AssertionError(message)
    checks.append(message)


def wait_until(predicate, seconds=6):
    start = time.monotonic()
    while time.monotonic() - start < seconds:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("UI/background condition did not become true")


class Provider:
    slow = False

    async def generate(self, request, emit):
        emit({"kind": "dispatched", "dispatched": True})
        for _ in range(100 if self.slow else 2):
            if request.cancel.is_set():
                raise ProviderError("CANCELLED", "测试传输已停止", dispatched=True)
            await asyncio.sleep(0.03)
        shots = [{"shot_id": "s1", "order": 1, "duration_ms": 40000, "narration": "正文",
                  "visual_prompt": "采样统计动画", "visual_type": "技术动画"}]
        document = {"title": "ADC 技术", "script": "旁白正文与技术结论。", "shots": shots}
        if request.stage == "topic":
            data = {"candidates": [{"candidate_id": "t1", "title": "ADC 抖动技术", "angle": "解释量化边界", "suggested_duration_ms": 40000}]}
        elif request.stage == "review":
            data = {"verdict": "approve", "issues": [], "revised_document": document}
        else:
            data = document
        body = {"task_id": request.task_id, "run_id": request.run_id, "stage": request.stage,
                "upstream_sha256": request.upstream_sha256, "data": data}
        emit({"kind": "text", "text": "显式离线测试传输的实时预览"})
        return TextResult(json.dumps(body, ensure_ascii=False), {"offline_test_only": True})


app = QApplication([])
app.setStyle("Fusion")
load_system_fonts()
engine = Engine(OUT / "data")
provider = Provider()
tasks = TaskService(engine.root, providers={"antigravity": provider, "gemini_web": provider})
window = Window(engine, tasks, auto_recommendations=False)
tasks.start()
window.show()
brief = {"domain": "嵌入式", "audience": "开发者", "aspect": "4:3", "target_ms": 40000}
p = engine.create_project("后台流程集成验收", brief)
window.open_project(p)
QTest.mouseClick(window.findChild(QPushButton, "advanceWorkflow"), Qt.MouseButton.LeftButton)
check(not tasks.submit(p), "同一项目在后台排队时拒绝重复提交")
wait_until(lambda: engine.step(p, "topic")["state"] == "WAITING_USER" and not tasks.busy())
app.processEvents()
check(window.stack.currentIndex() == 2 and window.candidate_table.rowCount() == 1, "后台候选通过Qt事件自动进入选题页")
window.candidate_table.selectRow(0)
app.processEvents()
check(window.topic_title.text() == "ADC 抖动技术", "选中候选会填入可编辑标题")
QTest.mouseClick(window.findChild(QPushButton, "confirmTopic"), Qt.MouseButton.LeftButton)
wait_until(lambda: engine.step(p, "final")["state"] == "WAITING_USER" and not tasks.busy())
app.processEvents()
check(engine.step(p, "draft")["state"] == "SUCCEEDED" and engine.step(p, "review")["state"] == "SUCCEEDED", "确认选题后后台连续执行初稿和审核")
check(window.doc_stage.currentData() == "final" and bool(window.editor.toPlainText()), "待确认终稿自动展示在编辑器")
QTest.mouseClick(window.findChild(QPushButton, "confirmDocument"), Qt.MouseButton.LeftButton)
wait_until(lambda: engine.step(p, "storyboard")["state"] == "WAITING_USER" and not tasks.busy())
app.processEvents()
check(window.stack.currentIndex() == 4 and window.shot_table.rowCount() == 1, "确认终稿后自动带入分镜页，保留镜头方案")
QTest.mouseClick(window.findChild(QPushButton, "confirmShots"), Qt.MouseButton.LeftButton)
wait_until(lambda: engine.step(p, "export")["state"] == "WAITING_USER" and not tasks.busy())
check(window.stack.currentIndex() == 8, "确认分镜后进入实际导出页")
window.navigate(1)
window.grab().save(str(OUT / "automatic-workflow.png"))

other = engine.create_project("后台取消验收", brief)
provider.slow = True
window.open_project(other)
QTest.mouseClick(window.findChild(QPushButton, "advanceWorkflow"), Qt.MouseButton.LeftButton)
wait_until(lambda: engine.step(other, "topic")["state"] == "RUNNING")
window.navigate(6)
check(window.stack.currentIndex() == 6, "后台等待期间界面仍可切换页面")
window._cancel()
wait_until(lambda: not tasks.busy())
check(engine.project(other)["job"]["state"] == "CANCELLED", "取消传递至后台，任务保留取消状态")
check(not engine.versions(other, "topic"), "取消后不发布迟到内容")
engine.resume(other)
check(engine.step(other, "topic")["state"] == "UNKNOWN", "取消后恢复仍保留已派发请求的未知外部状态")
check(engine.project(other)["job"]["state"] == "UNKNOWN", "恢复不会把未确认取消的外部请求变成可重发任务")
window.close()
wait_until(lambda: not tasks.isRunning())
app.processEvents()
check(not window.isVisible(), "关闭窗口会有序停止后台线程")
report = {"scope": "qt_background_integration_scripted_transports", "status": "passed", "model_calls": 0,
          "checks": checks, "evidence": str(OUT)}
(ROOT / "evidence/async-ui-acceptance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
