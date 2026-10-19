"""Focused daily-home acceptance. --live calls actual Antigravity once.

Default mode uses explicit scripted transports for selection, auto-folders and
export. Live mode uses production transports and verifies their real reference.
"""
import argparse
import asyncio
from datetime import date, timedelta
import json
import os
from pathlib import Path
import time
from uuid import uuid4

parser = argparse.ArgumentParser()
parser.add_argument("--live", action="store_true")
args = parser.parse_args()
if not args.live:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton
from video_app.config import load_settings
from video_app.core.contracts import TextResult
from video_app.core.daily import today
from video_app.core.engine import Engine
from video_app.core.workflow import WorkflowError
from video_app.ui.tasks import TaskService
from video_app.ui.theme import load_system_fonts
from video_app.ui.window import Window

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "evidence" / (("daily-live-" if args.live else "daily-offline-") + uuid4().hex[:10])
OUT.mkdir(parents=True)
load_settings()
app = QApplication([])
app.setStyle("Fusion")
load_system_fonts()
checks = []


class ScriptedProvider:
    def __init__(self):
        self.calls = []

    async def generate(self, request, emit):
        self.calls.append(request.stage)
        emit({"kind": "dispatched", "dispatched": True})
        await asyncio.sleep(0.05)
        if request.stage == "topic":
            data = {"candidates": [{"candidate_id": "t" + str(i), "title": title,
                "angle": "用一个可复现的小实验，讲清工作原理与适用边界。", "reason": "常见但容易误解，适合用技术动画解释。",
                "outline": ["先观察一个反直觉现象", "用波形与数据拆解原因", "给出工程使用边界"],
                "suggested_duration_ms": 40000} for i, title in enumerate((
                    "测试内容：ADC 加点噪声，为什么能测得更细？", "测试内容：串口数据为什么偶尔会丢一字节？", "测试内容：电机不转时，PID 积分在做什么？"))]}
        else:
            document = {"title": "离线验收文稿", "script": "这是显式测试正文，用于检查自动建目录和界面衔接。",
                "shots": [{"shot_id": "s1", "order": 1, "duration_ms": request.input_payload["brief"]["target_ms"],
                "narration": "这是显式测试正文。", "visual_prompt": "测试波形示意", "visual_type": "技术动画"}]}
            data = {"verdict": "approve", "issues": [], "revised_document": document} if request.stage == "review" else document
        raw = {"task_id": request.task_id, "run_id": request.run_id, "stage": request.stage,
               "upstream_sha256": request.upstream_sha256, "data": data}
        return TextResult(json.dumps(raw, ensure_ascii=False), {"scripted_test_only": True})


def check(value, message):
    if not value:
        raise AssertionError(message)
    checks.append(message)


def wait_until(predicate, seconds=230 if args.live else 8):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        app.processEvents()
        if predicate():
            return
        if not tasks.busy():
            failed = next((s for s in engine.steps(batch) if s["error_code"]), None)
            if failed:
                raise RuntimeError(f"{failed['stage']}: {failed['error_code']}")
        time.sleep(0.02)
    raise TimeoutError("等待每日推荐界面超时，未重复请求。")


def click(widget):
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton)
    app.processEvents()


engine = Engine(OUT / "data")
if not args.live:
    legacy = engine.create_project("已有作品", {"domain": "技术", "audience": "开发者", "target_ms": 40000, "aspect": "4:3"})
    legacy_artifact = engine.step(legacy, "brief")["output_artifact_id"]
    engine.db.executescript("DROP TABLE recommendation_works; DROP TABLE recommendation_days; PRAGMA user_version=2;")
    engine.close()
    engine = Engine(OUT / "data")
    check(engine.step(legacy, "brief")["output_artifact_id"] == legacy_artifact, "数据库升级保留已有作品与版本")
provider = ScriptedProvider() if not args.live else None
tasks = TaskService(engine.root, providers={"antigravity": provider, "gemini_web": provider} if provider else None)
window = Window(engine, tasks)
window.show()
tasks.start()
batch = window.daily.ensure()["project_id"]
report = {"scope": "real_antigravity_daily_ui" if args.live else "scripted_daily_ui",
          "status": "running", "checks": checks, "evidence": str(OUT)}
try:
    check(window.stack.currentIndex() == 9, "启动直接进入每日推荐，不要求新建项目")
    check([b.text() for b in window.nav.values()] == ["每日推荐", "我的作品", "创作工作台", "设置"], "主导航只有四个入口，没有01—09阶段")
    wait_until(lambda: bool(window.daily.contents()["candidates"]) and not tasks.busy())
    app.processEvents()
    value = window.daily.contents()
    check(len(value["candidates"]) == 3, "后台生成三项完整推荐内容")
    check(all(c.get("reason") and len(c.get("outline", [])) >= 3 for c in value["candidates"]), "推荐含理由与内容要点，不是空白选题表单")
    folder = engine.root / "daily" / today()
    check((folder / "recommendations.json").is_file() and (folder / "推荐选题.md").is_file(), "自动创建当天子文件夹并保存推荐")
    window.grab().save(str(OUT / "daily-home.png"))
    if args.live:
        version = engine.versions(batch, "topic")[0]
        run = engine._one("SELECT provider_ref_json FROM attempts WHERE id=?", (version["run_id"],))
        ref = json.loads(run["provider_ref_json"])
        check(bool(ref.get("conversation_id")) and ref.get("config_isolation_verified"), "推荐来自真实Antigravity调用，带会话与隔离凭据")
        report.update(provider_ref=ref, titles=[c["title"] for c in value["candidates"]], data_root=str(engine.root))
    else:
        check(provider.calls == ["topic"], "首次自动推荐只派发一次选题请求")
        window.navigate(0)
        check(all(p["id"] != batch for p in window.project_rows), "每日推荐内部记录不混入作品列表")
        window.navigate(9)
        QTest.qWait(150)
        check(provider.calls == ["topic"], "同日返回推荐读取缓存，不重复生成")
        choose = next(b for b in window.recommendations.scroll.findChildren(QPushButton) if b.property("candidateId") == "t0")
        click(choose)
        p = window.project_id
        wait_until(lambda: engine.step(p, "final")["state"] == "WAITING_USER" and not tasks.busy()
                   and window.stack.currentIndex() == 3 and window.doc_stage.currentData() == "final"
                   and bool(window.editor.toPlainText()))
        check(engine.step(p, "topic")["state"] == "SUCCEEDED", "点用这个选题即完成选择并自动准备文稿")
        check(window.daily.work_folder(p).is_relative_to(folder), "作品子文件夹自动归入推荐当天")
        check(window.daily.choose(today(), "t0", expected_artifact_id=value["artifact_id"]) == p, "重复选择同一推荐打开已有作品，不重复创建")
        click(window.findChild(QPushButton, "confirmDocument"))
        wait_until(lambda: engine.step(p, "storyboard")["state"] == "WAITING_USER" and not tasks.busy()
                   and window.stack.currentIndex() == 4 and window.shot_table.rowCount() > 0)
        click(window.findChild(QPushButton, "confirmShots"))
        wait_until(lambda: engine.step(p, "export")["state"] == "WAITING_USER" and not tasks.busy()
                   and window.stack.currentIndex() == 8)
        click(window.findChild(QPushButton, "exportProject"))
        exported = Path(engine.latest(p, "export", published=True)["folder"])
        check(exported.is_relative_to(folder) and (exported / "script.md").is_file(), "保存作品不弹目录选择，自动写入日期文件夹")
        window.navigate(1)
        check(not window.workflow_details.isVisible(), "九个技术节点默认收起，不作为用户主流程")
        window.navigate(9)
        choose = next(b for b in window.recommendations.scroll.findChildren(QPushButton) if b.property("candidateId") == "t0")
        before_calls = list(provider.calls)
        click(choose)
        check(window.stack.currentIndex() == 8 and window.project_id == p and provider.calls == before_calls,
              "再次选择已完成推荐直接打开作品，不重跑生成或退回初稿")
        tomorrow = (date.fromisoformat(today()) + timedelta(days=1)).isoformat()
        next_batch = window.daily.ensure(tomorrow)
        check(next_batch == window.daily.ensure(tomorrow) and (engine.root / "daily" / tomorrow).is_dir(), "次日自动新建独立子文件夹，同日不重复建项目")
        window.navigate(9)
        click(window.recommendations.generate)
        wait_until(lambda: len(provider.calls) == 4 and not tasks.busy())
        check(window.daily.contents()["artifact_id"] != value["artifact_id"], "换一批推荐生成新版本，已有作品保持独立")
        try:
            window.daily.choose(today(), "t0", expected_artifact_id=value["artifact_id"])
            raise AssertionError("旧推荐选择未被拒绝")
        except WorkflowError as error:
            check(error.code == "RECOMMENDATION_CHANGED", "不把更新前的推荐误选成新内容")
    report["status"] = "passed"
except Exception as error:
    report.update(status="failed", error=str(error), page=window.stack.currentIndex(),
                  document_stage=window.doc_stage.currentData(), notice=window.notice.text())
finally:
    window.closing = True
    tasks.shutdown()
    until = time.monotonic() + 20
    while tasks.isRunning() and time.monotonic() < until:
        app.processEvents()
        time.sleep(0.02)
    window.close()
    app.processEvents()
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (ROOT / "evidence" / ("daily-live-acceptance.json" if args.live else "daily-ui-acceptance.json")).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
raise SystemExit(0 if report["status"] == "passed" else 1)
